# worker/track_once.py — STATISTICS TRACKER (batch 50 IDs, milestones 1h→24h) + handle backfill
# ---------------------------------------------------------------------------------
# What it does:
#   - Pick videos that are due for a statistics re-poll (tracking.status="tracking"
#     and tracking.next_poll_after <= now).
#   - Call YouTube videos.list?part=statistics in batches of up to 50 IDs (1 unit/request).
#   - Append a timestamped snapshot into stats_snapshots, update poll counters,
#     and schedule the next milestone based on snippet.publishedAt.
#   - Backfill channel @handle (snippet.channelHandle & source.channelHandle) lazily with cache.
#   - When a video reaches >= 24h age (no more milestones), mark as complete.
#
# Quota:
#   - videos.list?part=statistics = 1 unit per request (up to 50 videoId).
#   - channels.list?part=snippet  = 1 unit per request (up to 50 channelId) — only when needed for handle backfill.
#
# Exit codes:
#   - 0  : success (may be "No due videos.")
#   - 88 : quota exhausted (quotaExceeded|dailyLimitExceeded|rateLimitExceeded...)
#   - 2  : missing YT_API_KEY
#   - 1  : other errors
#
# Environment:
#   Required:
#     - YT_API_KEY
#     - MONGO_URI                      (e.g., mongodb://localhost:27017/ytscan)
#   Optional:
#     - TRACK_BATCH_SIZE               (default: 50; hard limit: 50)
#     - TRACK_MAX_DUE_PER_RUN          (default: 1000)
#     - YT_TRACK_PLAN_MINUTES          (comma list; overrides default ML milestones)
#     - TRACK_LOG_SAMPLE               (default: 5; how many sample lines to print)
#     - YT_ENRICH_HANDLE_MODE          (default: "track"; "off" to disable; "discover" means discover_once handles it)
#
# Mongo indexes (highly recommended):
#   db.videos.createIndex({ "tracking.status": 1, "tracking.next_poll_after": 1 })
#   db.videos.createIndex({ "snippet.publishedAt": -1 })
#
from __future__ import annotations

import os, sys, io
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

import requests
from pymongo import MongoClient, UpdateOne
from dotenv import load_dotenv

# Ensure UTF-8 console logging (Windows PowerShell safety)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Load .env but DO NOT override existing ENV (server/cron wins)
load_dotenv(override=False)

# ---- Config ----
API_KEY   = os.getenv("YT_API_KEY")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")

TRACK_BATCH_SIZE = min(50, max(1, int(os.getenv("TRACK_BATCH_SIZE", "50"))))
TRACK_MAX_DUE    = max(1, int(os.getenv("TRACK_MAX_DUE_PER_RUN", "1000")))
LOG_SAMPLE       = max(0, int(os.getenv("TRACK_LOG_SAMPLE", "5")))

ENRICH_HANDLE_MODE = os.getenv("YT_ENRICH_HANDLE_MODE", "track").lower()  # track|discover|off

# Milestones (minutes since publishedAt) for 24h — tuned for early viral detection
# Default (when YT_TRACK_PLAN_MINUTES is not set):
#   0–2h   : every 5 minutes
#   2–6h   : every 15 minutes
#   6–12h  : every 30 minutes
#   12–24h : every 60 minutes
_PLAN_ENV = os.getenv("YT_TRACK_PLAN_MINUTES")
if _PLAN_ENV:
    PLAN_MINUTES = [int(x) for x in _PLAN_ENV.split(",") if x.strip()]
else:
    PLAN_MINUTES = (
        list(range(5, 120+1, 5)) +      # 0–2h
        list(range(135, 360+1, 15)) +   # 2–6h
        list(range(390, 720+1, 30)) +   # 6–12h
        list(range(780, 1440+1, 60))    # 12–24h
    )

VIDEOS_URL   = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
EXIT_QUOTA   = 88


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def next_due_from_publish(published_at: datetime, now: datetime) -> Optional[datetime]:
    """Return the next milestone time strictly > now, or None if >= last milestone (≈24h)."""
    age_min = (now - published_at).total_seconds() / 60.0
    # debug (first few milestones)
    try:
        print(f"[milestone] age={age_min:.1f}m | next> {next((m for m in PLAN_MINUTES if m>age_min), None)}")
    except Exception:
        pass
    for m in PLAN_MINUTES:
        due = published_at + timedelta(minutes=m)
        if due > now:
            return due
    return None  # no more milestones (>=24h)


def fetch_stats(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch statistics for up to 50 video IDs. Returns {videoId: statistics}."""
    if not video_ids:
        return {}
    params = {
        "key": API_KEY,
        "part": "statistics",
        "id": ",".join(video_ids[:50])
    }
    r = requests.get(VIDEOS_URL, params=params, timeout=30)
    r.raise_for_status()
    out: Dict[str, Dict[str, Any]] = {}
    for it in r.json().get("items", []):
        vid = it.get("id")
        if vid:
            out[vid] = it.get("statistics", {})
    return out


def fetch_channel_handles(channel_ids: List[str]) -> Dict[str, str]:
    """Return {channelId: '@handle'} via channels.list(snippet.customUrl)."""
    if not channel_ids:
        return {}
    params = {"key": API_KEY, "part": "snippet", "id": ",".join(channel_ids[:50])}
    r = requests.get(CHANNELS_URL, params=params, timeout=30)
    r.raise_for_status()
    out: Dict[str, str] = {}
    for it in r.json().get("items", []):
        cid = it.get("id")
        handle = (it.get("snippet") or {}).get("customUrl")
        if cid and handle:
            out[cid] = handle
    return out


def backfill_handles_for_due_videos(due_docs: List[Dict[str, Any]], db) -> None:
    """Enrich snippet.channelHandle & source.channelHandle (if missing), with cache in db.channels."""
    if ENRICH_HANDLE_MODE in ("off", "discover"):
        return
    need: List[str] = []
    for d in due_docs:
        sn = d.get("snippet", {}) or {}
        src = d.get("source", {}) or {}
        cid = sn.get("channelId")
        if not cid:
            continue
        if not sn.get("channelHandle") and not src.get("channelHandle"):
            need.append(cid)
    need = sorted(set(need))
    if not need:
        return

    # cache
    cached: Dict[str, str] = {c["_id"]: c.get("handle") for c in db.channels.find({"_id": {"$in": need}}, {"handle": 1})}
    to_fetch = [cid for cid in need if not cached.get(cid)]

    fetched: Dict[str, str] = {}
    for i in range(0, len(to_fetch), 50):
        batch = to_fetch[i:i+50]
        fetched.update(fetch_channel_handles(batch))

    handle_map = {**cached, **fetched}

    # write cache
    if fetched:
        now_iso = now_utc().isoformat()
        ops = [
            UpdateOne({"_id": cid}, {"$set": {"handle": h, "last_checked_at": now_iso}}, upsert=True)
            for cid, h in fetched.items()
        ]
        if ops:
            db.channels.bulk_write(ops, ordered=False)

    # update videos
    ops = []
    for d in due_docs:
        vid = d.get("_id")
        sn  = d.get("snippet", {}) or {}
        src = d.get("source", {}) or {}
        cid = sn.get("channelId")
        if not vid or not cid:
            continue
        h = handle_map.get(cid)
        if not h:
            continue
        ops.append(UpdateOne({"_id": vid}, {"$set": {
            "snippet.channelHandle": h,
            "source.channelHandle": src.get("channelHandle") or h
        }}))

    if ops:
        db.videos.bulk_write(ops, ordered=False)


def main() -> int:
    print(">>> track_once starting")
    if not API_KEY:
        print("Missing YT_API_KEY", file=sys.stderr)
        return 2

    client = MongoClient(MONGO_URI)
    db = client.get_database()
    now = now_utc()
    now_iso = now.isoformat()

    # 1) Find due videos by ISO timestamp (stored as ISO-8601 UTC string)
    due_cur = (db.videos.find({
        "tracking.status": "tracking",
        "tracking.next_poll_after": {"$lte": now_iso}
    }, {"_id": 1, "snippet.publishedAt": 1, "snippet.channelId": 1, "snippet.channelHandle": 1, "source.channelHandle": 1, "tracking": 1})
    .sort("tracking.next_poll_after", 1)
    .limit(TRACK_MAX_DUE))

    due_docs = list(due_cur)
    if not due_docs:
        print("No due videos.")
        return 0

    print(f"Due videos: {len(due_docs)}")
    print(f"Plan milestones (first 8): {PLAN_MINUTES[:8]}{' ...' if len(PLAN_MINUTES)>8 else ''}")

    # 1b) Lazy handle backfill (cached)
    backfill_handles_for_due_videos(due_docs, db)

    processed = 0
    completed = 0

    # 2) Process in batches (<=50 ids per request)
    for i in range(0, len(due_docs), TRACK_BATCH_SIZE):
        batch = due_docs[i:i + TRACK_BATCH_SIZE]
        ids = [str(d["_id"]) for d in batch]
        try:
            stats_map = fetch_stats(ids)
        except requests.HTTPError as e:
            # Handle possible quota errors explicitly
            try:
                body = e.response.json()
            except Exception:
                body = {"error": str(e)}
            reason = None
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                errs = err.get("errors") or []
                if isinstance(errs, list) and errs:
                    reason = errs[0].get("reason")
                reason = reason or err.get("status") or err.get("message")
            quota_reasons = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}
            if str(reason) in quota_reasons:
                print("YouTube quota exhausted — stopping tracker.", file=sys.stderr)
                return EXIT_QUOTA
            print("YouTube API error while fetching stats:", body, file=sys.stderr)
            return 1

        ops: List[UpdateOne] = []
        for d in batch:
            vid = str(d["_id"])
            sn = d.get("snippet", {}) or {}
            pub = parse_iso(sn.get("publishedAt") or "")
            if not pub:
                # No publish time => mark complete to avoid looping forever
                ops.append(UpdateOne({"_id": vid}, {
                    "$set": {
                        "tracking.status": "complete",
                        "tracking.stop_reason": "no_publishedAt",
                        "tracking.last_polled_at": now_iso,
                        "tracking.next_poll_after": None
                    },
                    "$inc": {"tracking.poll_count": 1}
                }))
                completed += 1
                continue

            st = stats_map.get(vid)
            if not st:
                # Video unavailable (deleted/private or no stats returned)
                ops.append(UpdateOne({"_id": vid}, {
                    "$set": {
                        "tracking.status": "complete",
                        "tracking.stop_reason": "unavailable",
                        "tracking.last_polled_at": now_iso,
                        "tracking.next_poll_after": None
                    },
                    "$inc": {"tracking.poll_count": 1}
                }))
                completed += 1
                continue

            # Build snapshot (likeCount/commentCount may be hidden/absent)
            snap = {
                "ts": now_iso,
                "viewCount": int(st.get("viewCount", 0) or 0),
                "likeCount": (int(st["likeCount"]) if "likeCount" in st else None),
                "commentCount": (int(st["commentCount"]) if "commentCount" in st else None)
            }

            # Compute next milestone
            next_due = next_due_from_publish(pub, now)
            if next_due is None:
                # done (>=24h)
                ops.append(UpdateOne({"_id": vid}, {
                    "$push": {"stats_snapshots": snap},
                    "$set": {
                        "tracking.status": "complete",
                        "tracking.stop_reason": "age>=24h",
                        "tracking.last_polled_at": now_iso,
                        "tracking.next_poll_after": None
                    },
                    "$inc": {"tracking.poll_count": 1}
                }))
                completed += 1
            else:
                ops.append(UpdateOne({"_id": vid}, {
                    "$push": {"stats_snapshots": snap},
                    "$set": {
                        "tracking.last_polled_at": now_iso,
                        "tracking.next_poll_after": next_due.isoformat()
                    },
                    "$inc": {"tracking.poll_count": 1}
                }))

        if ops:
            db.videos.bulk_write(ops, ordered=False)
        processed += len(batch)

    # Log some samples for visibility
    if LOG_SAMPLE and due_docs:
        print("Sample due items:")
        for d in due_docs[:LOG_SAMPLE]:
            print(f" - {d['_id']} | prev next_poll_after={d.get('tracking',{}).get('next_poll_after')}")

    print(f"Processed: {processed}, completed: {completed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
