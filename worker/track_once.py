# worker/track_once.py — STATISTICS TRACKER (batch 50 IDs, milestones 1h→24h)
# ---------------------------------------------------------------------------------
# What it does:
#   - Pick videos that are due for a statistics re-poll (tracking.status="tracking"
#     and tracking.next_poll_after <= now).
#   - Call YouTube videos.list?part=statistics in batches of up to 50 IDs (1 unit/request).
#   - Append a timestamped snapshot into stats_snapshots, update poll counters,
#     and schedule the next milestone based on snippet.publishedAt (1h .. 24h).
#   - When a video reaches >= 24h age (no more milestones), mark as complete.
#
# Quota:
#   - videos.list?part=statistics = 1 unit per request (up to 50 videoId).
#   - If N videos are due at the same time: cost ~= ceil(N/50) units for this run.
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
#     - MONGO_URI     (e.g., mongodb://localhost:27017/ytscan)
#   Optional:
#     - TRACK_BATCH_SIZE         (default: 50; hard limit: 50)
#     - TRACK_MAX_DUE_PER_RUN    (default: 1000)
#     - YT_TRACK_PLAN_MINUTES    (comma list; default: 60,120,...,1440)
#     - TRACK_LOG_SAMPLE         (default: 5; how many sample lines to print)
#
# Mongo indexes (highly recommended):
#   db.videos.createIndex({ "tracking.status": 1, "tracking.next_poll_after": 1 })
#   db.videos.createIndex({ "snippet.publishedAt": -1 })
#
from __future__ import annotations

import os, sys, io, math
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

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

# Milestones (minutes since publishedAt). Default: [60,120,...,1440]
_PLAN_ENV = os.getenv("YT_TRACK_PLAN_MINUTES")
if _PLAN_ENV:
    PLAN_MINUTES = [int(x) for x in _PLAN_ENV.split(",") if x.strip()]
else:
    PLAN_MINUTES = list(range(60, 1441, 60))

VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
EXIT_QUOTA = 88


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def next_due_from_publish(published_at: datetime, now: datetime) -> datetime | None:
    """Return the next milestone time strictly > now, or None if 24h passed."""
    for m in PLAN_MINUTES:
        due = published_at + timedelta(minutes=m)
        if due > now:
            return due
    return None  # no more milestones (>=24h)


def fetch_stats(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch statistics for up to 50 video IDs. Returns {videoId: {statistics}}."""
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


def main() -> int:
    print(">>> track_once starting")
    if not API_KEY:
        print("Missing YT_API_KEY", file=sys.stderr)
        return 2

    client = MongoClient(MONGO_URI)
    db = client.get_database()
    now = now_utc()
    now_iso = now.isoformat()

    # 1) Find due videos (string ISO compare works for RFC3339, but stored values must be ISO UTC)
    due_cur = (db.videos.find({
        "tracking.status": "tracking",
        "tracking.next_poll_after": {"$lte": now_iso}
    })
    .sort("tracking.next_poll_after", 1)
    .limit(TRACK_MAX_DUE))

    due_docs = list(due_cur)
    if not due_docs:
        print("No due videos.")
        return 0

    total = len(due_docs)
    print(f"Due videos: {total} | plan milestones (minutes): {PLAN_MINUTES[:6]}...{' (truncated)' if len(PLAN_MINUTES)>6 else ''}")

    processed = 0
    completed = 0

    # 2) Process in batches (<=50 ids per request)
    for i in range(0, total, TRACK_BATCH_SIZE):
        batch = due_docs[i:i + TRACK_BATCH_SIZE]
        ids = [str(d["_id"]) for d in batch]
        stats_map = {}
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
            return 2

        ops: List[UpdateOne] = []
        for d in batch:
            vid = str(d["_id"])
            sn = d.get("snippet", {})
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
                "likeCount": int(st["likeCount"]) if "likeCount" in st else None,
                "commentCount": int(st["commentCount"]) if "commentCount" in st else None
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
            print(f" - {d['_id']} | next_poll_after was {d.get('tracking',{}).get('next_poll_after')}")

    print(f"Processed: {processed}, completed: {completed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
