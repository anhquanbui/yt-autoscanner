# tools/backfill_missing_fields.py (v1.0) — one-shot BACKFILL tool (handles + duration)
# ---------------------------------------------------------------------------------
# PURPOSE
#   Chạy thủ công khi cần để backfill các trường còn thiếu cho video:
#   - Channel handle (@customUrl)  → snippet.channelHandle & source.channelHandle
#   - Duration (durationISO, durationSec) → và phân loại lengthBucket (short/medium/long/live)
#
#   ✅ Tách riêng khỏi track_once.py để không ảnh hưởng luồng tracking.
#   ✅ An toàn quota: chạy theo lô 50 ID, có LIMIT & filter rõ ràng.
#   ✅ Có DRY-RUN để test trước khi ghi DB.
#
# USAGE (PowerShell ví dụ):
#   $env:YT_API_KEY="..."
#   $env:MONGO_URI="mongodb://localhost:27017/ytscan"
#   # Chỉ backfill video đã complete, tối đa 800 items, bỏ qua live
#   $env:BF_TARGET="complete"
#   $env:BF_LIMIT="800"
#   $env:BF_SKIP_LIVE="1"
#   python tools/backfill_missing_fields.py
#
# ENV OPTIONS
#   Required:
#     - YT_API_KEY
#     - MONGO_URI              (vd: mongodb://localhost:27017/ytscan)
#
#   Optional:
#     - BF_TARGET              all | complete | tracking        (default: all)
#     - BF_LIMIT               số lượng tối đa tài liệu quét    (default: 1000)
#     - BF_BATCH_SIZE          batch size khi gọi API           (default: 50; max 50)
#     - BF_FILL_HANDLE         1/0 bật tắt backfill handle      (default: 1)
#     - BF_FILL_DURATION       1/0 bật tắt backfill duration    (default: 1)
#     - BF_SKIP_LIVE           1/0 bỏ qua item lengthBucket=live (default: 1)
#     - BF_LOG_SAMPLE          số dòng sample để in ra          (default: 5)
#     - BF_DRY_RUN             1/0 chỉ in log, KHÔNG ghi DB     (default: 0)
#
# Mongo indexes (khuyến nghị):
#   db.videos.createIndex({ "tracking.status": 1, "tracking.next_poll_after": 1 })
#   db.videos.createIndex({ "snippet.publishedAt": -1 })
#
from __future__ import annotations

import os, sys, io, re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
from pymongo import MongoClient, UpdateOne
from dotenv import load_dotenv

# Console UTF-8 safety
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

load_dotenv(override=False)

# ---- Config ----
API_KEY   = os.getenv("YT_API_KEY")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")

BF_TARGET        = os.getenv("BF_TARGET", "all").lower()           # all|complete|tracking
BF_LIMIT         = max(1, int(os.getenv("BF_LIMIT", "1000")))
BF_BATCH_SIZE    = min(50, max(1, int(os.getenv("BF_BATCH_SIZE", "50"))))
BF_FILL_HANDLE   = os.getenv("BF_FILL_HANDLE", "1").lower() in ("1","true","yes")
BF_FILL_DURATION = os.getenv("BF_FILL_DURATION", "1").lower() in ("1","true","yes")
BF_SKIP_LIVE     = os.getenv("BF_SKIP_LIVE", "1").lower() in ("1","true","yes")
BF_LOG_SAMPLE    = max(0, int(os.getenv("BF_LOG_SAMPLE", "5")))
BF_DRY_RUN       = os.getenv("BF_DRY_RUN", "0").lower() in ("1","true","yes")

VIDEOS_URL   = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
EXIT_QUOTA   = 88

# --- Duration helpers ---
_DUR_RE = re.compile(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$', re.I)

def iso8601_to_seconds(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = _DUR_RE.match(s)
    if not m:
        return None
    h, mnt, sec = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mnt * 60 + sec

def bucket_from_seconds(secs: Optional[int]) -> Optional[str]:
    if secs is None:
        return None
    if secs < 4*60:
        return "short"
    if secs <= 20*60:
        return "medium"
    return "long"

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# --- API helpers ---
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

def fetch_video_details(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Return {videoId: {contentDetails, liveStreamingDetails}}"""
    if not video_ids:
        return {}
    params = {"key": API_KEY, "part": "contentDetails,liveStreamingDetails", "id": ",".join(video_ids[:50])}
    r = requests.get(VIDEOS_URL, params=params, timeout=30)
    r.raise_for_status()
    out: Dict[str, Dict[str, Any]] = {}
    for it in r.json().get("items", []):
        vid = it.get("id")
        if vid:
            out[vid] = {
                "contentDetails": it.get("contentDetails", {}) or {},
                "liveStreamingDetails": it.get("liveStreamingDetails", {}) or {},
            }
    return out

# --- Backfill ops ---
def backfill_handles(candidates: List[Dict[str, Any]], db) -> None:
    if not BF_FILL_HANDLE:
        return

    need: List[str] = []
    for d in candidates:
        sn = d.get("snippet", {}) or {}
        src = d.get("source", {}) or {}
        cid = sn.get("channelId")
        if not cid:
            continue
        if not sn.get("channelHandle") and not src.get("channelHandle"):
            need.append(cid)
    need = sorted(set(need))
    if not need:
        print("Handles: nothing to backfill.")
        return

    # Cache
    cached: Dict[str, str] = {c["_id"]: c.get("handle") for c in db.channels.find({"_id": {"$in": need}}, {"handle": 1})}
    to_fetch = [cid for cid in need if not cached.get(cid)]

    fetched: Dict[str, str] = {}
    for i in range(0, len(to_fetch), 50):
        batch = to_fetch[i:i+50]
        try:
            fetched.update(fetch_channel_handles(batch))
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
            if str(reason) in {"quotaExceeded","dailyLimitExceeded","rateLimitExceeded","userRateLimitExceeded"}:
                print("Handles: quota exhausted — stop.", file=sys.stderr)
                raise SystemExit(EXIT_QUOTA)
            print("Handles: YouTube API error:", body, file=sys.stderr)
            raise SystemExit(1)

    handle_map = {**cached, **fetched}

    # write cache
    if fetched and not BF_DRY_RUN:
        now_iso = now_utc_iso()
        ops = [
            UpdateOne({"_id": cid}, {"$set": {"handle": h, "last_checked_at": now_iso}}, upsert=True)
            for cid, h in fetched.items()
        ]
        if ops:
            db.channels.bulk_write(ops, ordered=False)

    # update videos
    ops = []
    for d in candidates:
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
        if BF_DRY_RUN:
            print(f"[DRY-RUN] Would update handles for {len(ops)} videos")
        else:
            db.videos.bulk_write(ops, ordered=False)
            print(f"Handles: updated {len(ops)} videos.")

def backfill_duration(candidates: List[Dict[str, Any]], db) -> None:
    if not BF_FILL_DURATION:
        return

    # pick ids missing duration or bucket
    need: List[str] = []
    for d in candidates:
        sn = d.get("snippet", {}) or {}
        if BF_SKIP_LIVE and sn.get("lengthBucket") == "live":
            continue
        if (not sn.get("durationISO")) or (not sn.get("lengthBucket")):
            need.append(str(d["_id"]))

    if not need:
        print("Duration: nothing to backfill.")
        return

    updated = 0
    for i in range(0, len(need), 50):
        batch = need[i:i+50]
        try:
            det = fetch_video_details(batch)
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
            if str(reason) in {"quotaExceeded","dailyLimitExceeded","rateLimitExceeded","userRateLimitExceeded"}:
                print("Duration: quota exhausted — stop.", file=sys.stderr)
                raise SystemExit(EXIT_QUOTA)
            print("Duration: YouTube API error:", body, file=sys.stderr)
            raise SystemExit(1)

        ops = []
        for vid in batch:
            d = det.get(vid) or {}
            cd = d.get("contentDetails", {}) or {}
            lsd = d.get("liveStreamingDetails", {}) or {}

            dur_iso = cd.get("duration")
            dur_sec = iso8601_to_seconds(dur_iso) if dur_iso else None

            length_bucket = None
            if dur_sec is not None:
                length_bucket = bucket_from_seconds(dur_sec)
            elif lsd.get("actualStartTime") or lsd.get("scheduledStartTime"):
                length_bucket = "live"

            update_fields: Dict[str, Any] = {}
            if dur_iso:
                update_fields["snippet.durationISO"] = dur_iso
            if dur_sec is not None:
                update_fields["snippet.durationSec"] = dur_sec
            if length_bucket and (not BF_SKIP_LIVE or length_bucket != "live"):
                update_fields["snippet.lengthBucket"] = length_bucket

            if update_fields:
                if BF_DRY_RUN:
                    print(f"[DRY-RUN] {vid} set {update_fields}")
                else:
                    ops.append(UpdateOne({"_id": vid}, {"$set": update_fields}))

        if ops:
            if BF_DRY_RUN:
                print(f"[DRY-RUN] Would update {len(ops)} videos in this batch")
            else:
                db.videos.bulk_write(ops, ordered=False)
                updated += len(ops)

    if not BF_DRY_RUN:
        print(f"Duration: updated {updated} videos.")

# --- Query candidates ---
def build_query() -> Dict[str, Any]:
    status_filter = {}
    if BF_TARGET == "complete":
        status_filter = {"tracking.status": "complete"}
    elif BF_TARGET == "tracking":
        status_filter = {"tracking.status": "tracking"}
    else:
        status_filter = {"tracking.status": {"$in": ["tracking", "complete"]}}

    # missing conditions
    # Note: tạo cấu trúc $or/$and phù hợp với tùy chọn bật/tắt
    ors = []
    ands = [status_filter]

    if BF_FILL_HANDLE:
        ors.extend([
            {"snippet.channelHandle": {"$exists": False}},
            {"source.channelHandle": {"$exists": False}},
        ])

    if BF_FILL_DURATION:
        dur_missing = [
            {"snippet.durationISO": {"$exists": False}},
            {"snippet.lengthBucket": {"$exists": False}},
        ]
        if BF_SKIP_LIVE:
            ands.append({"snippet.lengthBucket": {"$ne": "live"}})
        ors.extend(dur_missing)

    if not ors:
        # không có nhu cầu backfill nào → trả về query không match
        return {"_id": None}

    return {"$and": ands + [{"$or": ors}]}

def main() -> int:
    print(">>> backfill_missing_fields starting")
    if not API_KEY:
        print("Missing YT_API_KEY", file=sys.stderr)
        return 2
    client = MongoClient(MONGO_URI)
    db = client.get_database()

    query = build_query()
    proj = {
        "_id": 1,
        "source.channelHandle": 1,
        "snippet.channelId": 1,
        "snippet.channelHandle": 1,
        "snippet.durationISO": 1,
        "snippet.durationSec": 1,
        "snippet.lengthBucket": 1,
        "tracking.status": 1,
    }
    cur = db.videos.find(query, proj).sort([("_id", 1)]).limit(BF_LIMIT)
    candidates = list(cur)
    print(f"Candidates: {len(candidates)} (target={BF_TARGET}, limit={BF_LIMIT})")

    if not candidates:
        print("Nothing to backfill.")
        return 0

    if BF_LOG_SAMPLE and candidates:
        print("Sample:")
        for d in candidates[:BF_LOG_SAMPLE]:
            sn = d.get("snippet", {}) or {}
            print(f" - {d['_id']} | status={d.get('tracking',{}).get('status')} | handle={sn.get('channelHandle')} | len={sn.get('lengthBucket')} | durISO={sn.get('durationISO')}")

    # 1) Handles
    try:
        backfill_handles(candidates, db)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_QUOTA

    # 2) Duration
    try:
        backfill_duration(candidates, db)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_QUOTA

    print("Backfill done.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
