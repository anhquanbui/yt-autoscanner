
# worker/track_once.py (v3.1) — STATISTICS TRACKER (batch 50 IDs, milestones 1h→24h) + handle backfill + duration backfill
# (see header in previous message for details)
from __future__ import annotations

import os, sys, io, re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

import requests
from pymongo import MongoClient, UpdateOne
from dotenv import load_dotenv
from pathlib import Path

# Ensure UTF-8 console logging (Windows PowerShell safety)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Load .env but DO NOT override existing ENV (server/cron wins)
def load_project_env():
    loaded = False

    # 1) Ưu tiên .env ở root dự án (…/yt-autoscanner/.env)
    proj_root = Path(__file__).resolve().parents[1] / ".env"
    if proj_root.exists():
        load_dotenv(dotenv_path=proj_root)
        print(f"✅ Loaded .env from project root: {proj_root}")
        loaded = True

    # 2) Thử .env ở HOME (ví dụ /home/ytscan/.env)
    home_env = Path.home() / ".env"
    if home_env.exists():
        load_dotenv(dotenv_path=home_env, override=True)
        print(f"✅ Loaded .env from home: {home_env}")
        loaded = True

    # 3) Thử .env tại thư mục đang chạy (CWD)
    cwd_env = Path(".env").resolve()
    if cwd_env.exists():
        load_dotenv(dotenv_path=cwd_env, override=True)
        print(f"✅ Loaded .env from CWD: {cwd_env}")
        loaded = True

    if not loaded:
        print("⚠️ No .env found. Using existing environment variables.")

load_project_env()


# ---- Config ----
API_KEY   = os.getenv("YT_API_KEY")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")

TRACK_BATCH_SIZE = min(50, max(1, int(os.getenv("TRACK_BATCH_SIZE", "50"))))
TRACK_MAX_DUE    = max(1, int(os.getenv("TRACK_MAX_DUE_PER_RUN", "1000")))
LOG_SAMPLE       = max(0, int(os.getenv("TRACK_LOG_SAMPLE", "5")))

ENRICH_HANDLE_MODE = os.getenv("YT_ENRICH_HANDLE_MODE", "track").lower()  # track|discover|off

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


def fetch_channel_handles(channel_ids: List[str]) -> Dict[str, str]:
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

    cached: Dict[str, str] = {c["_id"]: c.get("handle") for c in db.channels.find({"_id": {"$in": need}}, {"handle": 1})}
    to_fetch = [cid for cid in need if not cached.get(cid)]

    fetched: Dict[str, str] = {}
    for i in range(0, len(to_fetch), 50):
        batch = to_fetch[i:i+50]
        fetched.update(fetch_channel_handles(batch))

    handle_map = {**cached, **fetched}

    if fetched:
        now_iso = now_utc().isoformat()
        ops = [
            UpdateOne({"_id": cid}, {"$set": {"handle": h, "last_checked_at": now_iso}}, upsert=True)
            for cid, h in fetched.items()
        ]
        if ops:
            db.channels.bulk_write(ops, ordered=False)

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

    due_cur = (db.videos.find({
        "tracking.status": "tracking",
        "tracking.next_poll_after": {"$lte": now_iso}
    }, {"_id": 1, "snippet.publishedAt": 1, "snippet.channelId": 1, "snippet.channelHandle": 1, "source.channelHandle": 1, "tracking": 1, "snippet.durationISO": 1, "snippet.lengthBucket": 1})
    .sort("tracking.next_poll_after", 1)
    .limit(TRACK_MAX_DUE))

    due_docs = list(due_cur)
    if not due_docs:
        print("No due videos.")
        return 0

    print(f"Due videos: {len(due_docs)}")
    print(f"Plan milestones (first 8): {PLAN_MINUTES[:8]}{' ...' if len(PLAN_MINUTES)>8 else ''}")

    backfill_handles_for_due_videos(due_docs, db)

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
            sn = d.get("snippet", {}) or {}
            pub = parse_iso(sn.get("publishedAt") or "")
            if not pub:
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
