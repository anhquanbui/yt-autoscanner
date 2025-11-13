#!/usr/bin/env python3
# worker/track_once.py (v3.2) — STATISTICS TRACKER (batch 50 IDs, milestones 1h→24h)
# CHANGELOG (v3.2):
#   - Removed channel handle backfill & any writes to `db.channels`.
#   - Kept channelId usage for projections only; no enrichment of channel data.
#   - All other behaviors preserved: milestones scheduling, stats snapshots, duration backfill, quota handling.

from __future__ import annotations

import os, sys, io, re
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
TRACK_MAX_DUE    = max(1, int(os.getenv("TRACK_MAX_DUE_PER_RUN", "5000")))
LOG_SAMPLE       = max(0, int(os.getenv("TRACK_LOG_SAMPLE", "5")))

# Milestone plan (minutes since publishedAt)
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

# YouTube Data API endpoints
VIDEOS_URL   = "https://www.googleapis.com/youtube/v3/videos"
EXIT_QUOTA   = 88

# --- Duration helpers (for backfill) ---
_DUR_RE = re.compile(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$', re.I)

def iso8601_to_seconds(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = _DUR_RE.match(s)
    if not m:
        return None
    h, mnt, sec = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mnt * 60 + sec


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def next_due_from_publish(published_at: datetime, now: datetime) -> Optional[datetime]:
    age_min = (now - published_at).total_seconds() / 60.0
    try:
        print(f"[milestone] age={age_min:.1f}m | next> {next((m for m in PLAN_MINUTES if m>age_min), None)}")
    except Exception:
        pass
    for m in PLAN_MINUTES:
        due = published_at + timedelta(minutes=m)
        if due > now:
            return due
    return None


def fetch_stats(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not video_ids:
        return {}
    params = {"key": API_KEY, "part": "statistics", "id": ",".join(video_ids[:50])}
    r = requests.get(VIDEOS_URL, params=params, timeout=30)
    r.raise_for_status()
    out: Dict[str, Dict[str, Any]] = {}
    for it in r.json().get("items", []):
        vid = it.get("id")
        if vid:
            out[vid] = it.get("statistics", {})
    return out


def enrich_duration_for_missing_videos(due_docs: List[Dict[str, Any]], db) -> None:
    missing_ids = []
    for d in due_docs:
        sn = d.get("snippet", {}) or {}
        if not sn.get("durationISO") or not sn.get("lengthBucket"):
            missing_ids.append(str(d["_id"]))
    if not missing_ids:
        return

    print(f"Backfilling duration for {len(missing_ids)} videos...")
    for i in range(0, len(missing_ids), 50):
        batch = missing_ids[i:i+50]
        params = {
            "key": API_KEY,
            "part": "contentDetails,liveStreamingDetails",
            "id": ",".join(batch)
        }
        r = requests.get(VIDEOS_URL, params=params, timeout=30)
        r.raise_for_status()

        items = r.json().get("items", [])
        if not items:
            continue

        ops = []
        for it in items:
            vid = it.get("id")
            cd = it.get("contentDetails", {}) or {}
            lsd = it.get("liveStreamingDetails", {}) or {}

            dur_iso = cd.get("duration")
            dur_sec = iso8601_to_seconds(dur_iso) if dur_iso else None

            length_bucket = None
            if dur_sec is not None:
                if dur_sec < 240:
                    length_bucket = "short"
                elif dur_sec <= 1200:
                    length_bucket = "medium"
                else:
                    length_bucket = "long"
            elif lsd.get("actualStartTime") or lsd.get("scheduledStartTime"):
                length_bucket = "live"

            update_fields = {}
            if dur_iso:
                update_fields["snippet.durationISO"] = dur_iso
            if dur_sec is not None:
                update_fields["snippet.durationSec"] = dur_sec
            if length_bucket:
                update_fields["snippet.lengthBucket"] = length_bucket

            if update_fields:
                ops.append(UpdateOne({"_id": vid}, {"$set": update_fields}))

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

    # NOTE: We still project snippet.channelId for internal use/filters, but do not fetch/write any channel data.
    due_cur = (db.videos.find({
        "tracking.status": "tracking",
        "tracking.next_poll_after": {"$lte": now_iso}
    }, {
        "_id": 1, 
        "snippet.publishedAt": 1, 
        "snippet.channelId": 1, 
        "tracking": 1, 
        "snippet.durationISO": 1, 
        "snippet.lengthBucket": 1,
        "ml_flags": 1,
        })
    .sort("tracking.next_poll_after", 1)
    .limit(TRACK_MAX_DUE))

    due_docs = list(due_cur)
    if not due_docs:
        print("No due videos.")
        return 0

    print(f"Due videos: {len(due_docs)}")
    print(f"Plan milestones (first 8): {PLAN_MINUTES[:8]}{' ...' if len(PLAN_MINUTES)>8 else ''}")

    # Duration backfill (safe; no channel writes)
    try:
        enrich_duration_for_missing_videos(due_docs, db)
    except requests.HTTPError as e:
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
            print("YouTube quota exhausted during duration backfill — continuing stats only.", file=sys.stderr)
        else:
            print("YouTube API error during duration backfill:", body, file=sys.stderr)

    processed = 0
    completed = 0

    for i in range(0, len(due_docs), TRACK_BATCH_SIZE):
        batch = due_docs[i:i + TRACK_BATCH_SIZE]
        ids = [str(d["_id"]) for d in batch]
        try:
            stats_map = fetch_stats(ids)
        except requests.HTTPError as e:
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
            
            # 1) if flagged low_quality_v3_6h, stop track
            mlf = (d.get("ml_flags") or {}).get("low_quality_v3_6h") or {}
            if mlf.get("is_low") in (True, 1):
                ops.append(UpdateOne(
                    {"_id": vid},
                    {
                        "$set": {
                            "tracking.status": "stopped",
                            "tracking.stop_reason": "ml.low_quality_v3_6h",
                            "tracking.last_polled_at": now_iso,
                            "tracking.next_poll_after": None,
                        },
                        "$inc": {"tracking.poll_count": 1},
                    },
                ))
                completed += 1
                continue  # stop fetching stats for this video
            
            # 2) Normal process: parse publishedAt & continue to process
            sn = d.get("snippet", {}) or {}
            pub = parse_iso(sn.get("publishedAt") or "")
            if not pub:
                ops.append(UpdateOne(
                    {"_id": vid},
                    {
                        "$set": {
                            "tracking.status": "complete",
                            "tracking.stop_reason": "no_publishedAt",
                            "tracking.last_polled_at": now_iso,
                            "tracking.next_poll_after": None,
                        },
                        "$inc": {"tracking.poll_count": 1},
                    },
                ))
                completed += 1
                continue
            st = stats_map.get(vid)
            if not st:
                ops.append(UpdateOne({"_id": vid}, {
                    "$set": {
                        "tracking.status": "complete",
                        "tracking.stop_reason": "removed",  # updated
                        "tracking.last_polled_at": now_iso,
                        "tracking.next_poll_after": None
                    },
                    "$inc": {"tracking.poll_count": 1}
                }))
                completed += 1
                continue

            snap = {
                "ts": now_iso,
                "viewCount": int(st.get("viewCount", 0) or 0),
                "likeCount": (int(st["likeCount"]) if "likeCount" in st else None),
                "commentCount": (int(st["commentCount"]) if "commentCount" in st else None)
            }

            next_due = next_due_from_publish(pub, now)
            if next_due is None:
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

    if LOG_SAMPLE and due_docs:
        print("Sample due items:")
        for d in due_docs[:LOG_SAMPLE]:
            print(f" - {d['_id']} | prev next_poll_after={d.get('tracking',{}).get('next_poll_after')}")

    print(f"Processed: {processed}, completed: {completed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
