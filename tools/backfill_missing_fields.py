#!/usr/bin/env python3
"""
tools/backfill_missing_fields.py (v2.0) — one-shot BACKFILL tool (handles + duration)

Purpose
-------
Run manually when you want to backfill missing fields on `videos` documents:

  - Channel handle (@customUrl)
        → snippet.channelHandle
        → source.channelHandle

  - Duration (durationISO, durationSec)
        → snippet.durationISO
        → snippet.durationSec
        → snippet.lengthBucket  (short / medium / long / live)

Key properties
--------------
  ✅ Completely separated from track_once.py (does not affect tracking loop).
  ✅ Quota-safe: batches of 50 IDs (YouTube API limit).
  ✅ Has DRY-RUN mode to inspect changes before writing to MongoDB.

Environment variables
---------------------
Required:
  - YT_API_KEY
  - MONGO_URI      (e.g. mongodb://localhost:27017/ytscan)

Optional:
  - BF_TARGET        all | complete | tracking           (default: all)
  - BF_LIMIT         max number of docs to scan          (default: 1000)
  - BF_BATCH_SIZE    batch size for API calls (<= 50)    (default: 50)
  - BF_FILL_HANDLE   1/0 toggle channel handle backfill  (default: 1)
  - BF_FILL_DURATION 1/0 toggle duration backfill        (default: 1)
  - BF_SKIP_LIVE     1/0 skip items with lengthBucket=live (default: 1)
  - BF_LOG_SAMPLE    number of sample docs to print      (default: 5)
  - BF_DRY_RUN       1/0 log only, DO NOT write to DB    (default: 0)

Usage (recommended)
-------------------
Set env (PowerShell example):

    $env:YT_API_KEY="..."
    $env:MONGO_URI="mongodb://localhost:27017/ytscan"
    $env:BF_TARGET="complete"
    $env:BF_LIMIT="800"
    $env:BF_SKIP_LIVE="1"

Run as a module from project root:

    python -m tools.backfill_missing_fields
"""

from __future__ import annotations

import os
import sys
import io
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
from pymongo import MongoClient, UpdateOne

from config.env import load_env, get_env

# ------------------------------------------------------------------------------
# Console UTF-8 safety (Windows PowerShell etc.)
# ------------------------------------------------------------------------------
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ------------------------------------------------------------------------------
# Environment loading (via config.env)
# ------------------------------------------------------------------------------
load_env()  # idempotent; respects priority search from env.py


def _env_flag(name: str, default: str = "0") -> bool:
    """Return True/False for a boolean-like env var (1/true/yes)."""
    val = (get_env(name, default) or "").strip().lower()
    return val in ("1", "true", "yes")


def _env_int(name: str, default: str) -> int:
    """Read an int env var with a safe string default."""
    raw = get_env(name, default) or default
    try:
        return int(raw)
    except Exception:
        return int(default)


# ---- Config ----
API_KEY = get_env("YT_API_KEY")
MONGO_URI = get_env("MONGO_URI", "mongodb://localhost:27017/ytscan")

BF_TARGET = (get_env("BF_TARGET", "all") or "all").lower()   # all|complete|tracking
BF_LIMIT = max(1, _env_int("BF_LIMIT", "1000"))
BF_BATCH_SIZE = min(50, max(1, _env_int("BF_BATCH_SIZE", "50")))

BF_FILL_HANDLE = _env_flag("BF_FILL_HANDLE", "1")
BF_FILL_DURATION = _env_flag("BF_FILL_DURATION", "1")
BF_SKIP_LIVE = _env_flag("BF_SKIP_LIVE", "1")
BF_LOG_SAMPLE = max(0, _env_int("BF_LOG_SAMPLE", "5"))
BF_DRY_RUN = _env_flag("BF_DRY_RUN", "0")

VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
EXIT_QUOTA = 88

# ------------------------------------------------------------------------------
# Duration helpers
# ------------------------------------------------------------------------------
_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", re.I)


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
    if secs < 4 * 60:
        return "short"
    if secs <= 20 * 60:
        return "medium"
    return "long"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------------------
# YouTube API helpers
# ------------------------------------------------------------------------------
def _handle_quota_http_error(e: requests.HTTPError, context: str) -> None:
    """Inspect an HTTPError and exit with EXIT_QUOTA if it's a quota issue."""
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

    quota_reasons = {
        "quotaExceeded",
        "dailyLimitExceeded",
        "rateLimitExceeded",
        "userRateLimitExceeded",
    }

    if str(reason) in quota_reasons:
        print(f"[ERROR] YouTube quota exhausted during {context}.", file=sys.stderr)
        raise SystemExit(EXIT_QUOTA)
    else:
        print(f"[ERROR] YouTube API error during {context}: {body}", file=sys.stderr)
        raise


def fetch_channel_handles(channel_ids: List[str]) -> Dict[str, str]:
    """
    Call channels.list(snippet) and return a map: {channelId: '@handle'}.

    We rely on snippet.customUrl, which is the @handle / legacy custom URL.
    """
    if not channel_ids:
        return {}

    params = {
        "key": API_KEY,
        "part": "snippet",
        "id": ",".join(channel_ids[:50]),
    }

    try:
        r = requests.get(CHANNELS_URL, params=params, timeout=30)
        r.raise_for_status()
    except requests.HTTPError as e:
        _handle_quota_http_error(e, "fetch_channel_handles")
        return {}

    out: Dict[str, str] = {}
    for it in r.json().get("items", []):
        cid = it.get("id")
        handle = (it.get("snippet") or {}).get("customUrl")
        if cid and handle:
            out[cid] = handle
    return out


def fetch_durations(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Call videos.list(contentDetails, liveStreamingDetails) and return:

      {
        videoId: {
          "durationISO": "PT...",
          "durationSec": 123,
          "lengthBucket": "short|medium|long|live",
        },
        ...
      }
    """
    if not video_ids:
        return {}

    params = {
        "key": API_KEY,
        "part": "contentDetails,liveStreamingDetails",
        "id": ",".join(video_ids[:50]),
    }

    try:
        r = requests.get(VIDEOS_URL, params=params, timeout=30)
        r.raise_for_status()
    except requests.HTTPError as e:
        _handle_quota_http_error(e, "fetch_durations")
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for it in r.json().get("items", []):
        vid = it.get("id")
        cd = it.get("contentDetails") or {}
        lsd = it.get("liveStreamingDetails") or {}

        dur_iso = cd.get("duration")
        dur_sec = iso8601_to_seconds(dur_iso) if dur_iso else None

        length_bucket: Optional[str] = None
        if dur_sec is not None:
            length_bucket = bucket_from_seconds(dur_sec)
        elif lsd.get("actualStartTime") or lsd.get("scheduledStartTime"):
            length_bucket = "live"

        if vid:
            out[vid] = {
                "durationISO": dur_iso,
                "durationSec": dur_sec,
                "lengthBucket": length_bucket,
            }

    return out


# ------------------------------------------------------------------------------
# Backfill handlers
# ------------------------------------------------------------------------------
def backfill_channel_handles(candidates: List[Dict[str, Any]], db) -> None:
    """Backfill snippet.channelHandle & source.channelHandle if enabled."""
    if not BF_FILL_HANDLE:
        return

    channel_ids: List[str] = []
    for d in candidates:
        sn = d.get("snippet") or {}
        src = d.get("source") or {}
        if BF_FILL_HANDLE:
            if not sn.get("channelHandle") or not src.get("channelHandle"):
                cid = sn.get("channelId")
                if cid:
                    channel_ids.append(cid)

    channel_ids = sorted(set(channel_ids))
    if not channel_ids:
        print("[INFO] No candidates need channelHandle backfill.")
        return

    print(f"[INFO] Backfilling handles for {len(channel_ids)} unique channels...")

    handle_map: Dict[str, str] = {}
    for i in range(0, len(channel_ids), BF_BATCH_SIZE):
        batch = channel_ids[i : i + BF_BATCH_SIZE]
        batch_map = fetch_channel_handles(batch)
        handle_map.update(batch_map)
        print(f"[INFO]   fetched {len(batch_map)} handles (batch {i//BF_BATCH_SIZE+1})")

    ops: List[UpdateOne] = []
    for d in candidates:
        vid = str(d.get("_id"))
        sn = d.get("snippet") or {}
        src = d.get("source") or {}
        cid = sn.get("channelId")
        if not vid or not cid:
            continue

        handle = handle_map.get(cid)
        if not handle:
            continue

        update_fields: Dict[str, Any] = {}
        if not sn.get("channelHandle"):
            update_fields["snippet.channelHandle"] = handle
        if not src.get("channelHandle"):
            update_fields["source.channelHandle"] = handle

        if update_fields:
            ops.append(UpdateOne({"_id": vid}, {"$set": update_fields}))

    if not ops:
        print("[INFO] No handle updates to apply.")
        return

    if BF_DRY_RUN:
        print(f"[DRY-RUN] Would update handles for {len(ops)} videos")
    else:
        db.videos.bulk_write(ops, ordered=False)
        print(f"[INFO] Handles: updated {len(ops)} videos.")


def backfill_duration(candidates: List[Dict[str, Any]], db) -> None:
    """Backfill durationISO / durationSec / lengthBucket if enabled."""
    if not BF_FILL_DURATION:
        return

    need_ids: List[str] = []
    for d in candidates:
        vid = str(d.get("_id"))
        sn = d.get("snippet") or {}
        if not vid:
            continue

        # Skip explicit live if requested
        if BF_SKIP_LIVE and sn.get("lengthBucket") == "live":
            continue

        # Need duration if ISO or lengthBucket is missing
        if not sn.get("durationISO") or not sn.get("lengthBucket"):
            need_ids.append(vid)

    need_ids = sorted(set(need_ids))
    if not need_ids:
        print("[INFO] No candidates need duration backfill.")
        return

    print(f"[INFO] Backfilling duration for {len(need_ids)} videos...")

    ops: List[UpdateOne] = []
    for i in range(0, len(need_ids), BF_BATCH_SIZE):
        batch = need_ids[i : i + BF_BATCH_SIZE]
        dur_map = fetch_durations(batch)
        print(f"[INFO]   fetched durations for {len(dur_map)} videos (batch {i//BF_BATCH_SIZE+1})")

        for vid in batch:
            info = dur_map.get(vid)
            if not info:
                continue

            set_fields: Dict[str, Any] = {}

            if info.get("durationISO"):
                set_fields["snippet.durationISO"] = info["durationISO"]
            if info.get("durationSec") is not None:
                set_fields["snippet.durationSec"] = info["durationSec"]
            if info.get("lengthBucket"):
                set_fields["snippet.lengthBucket"] = info["lengthBucket"]

            if set_fields:
                ops.append(UpdateOne({"_id": vid}, {"$set": set_fields}))

    if not ops:
        print("[INFO] No duration updates to apply.")
        return

    if BF_DRY_RUN:
        print(f"[DRY-RUN] Would update duration for {len(ops)} videos")
    else:
        db.videos.bulk_write(ops, ordered=False)
        print(f"[INFO] Duration: updated {len(ops)} videos.")


# ------------------------------------------------------------------------------
# Query builder
# ------------------------------------------------------------------------------
def build_query() -> Dict[str, Any]:
    """
    Build the MongoDB filter used to select candidate videos for backfill.

    - BF_TARGET:
        * all       → {"tracking.status": {"$in": ["complete", "tracking"]}}
        * complete  → {"tracking.status": "complete"}
        * tracking  → {"tracking.status": "tracking"}
    - BF_FILL_HANDLE:
        require any of snippet.channelHandle or source.channelHandle missing.
    - BF_FILL_DURATION:
        require any of snippet.durationISO or snippet.lengthBucket missing.
        If BF_SKIP_LIVE, we also add a filter lengthBucket != "live".
    """
    # base status filter
    if BF_TARGET == "complete":
        status_filter: Dict[str, Any] = {"tracking.status": "complete"}
    elif BF_TARGET == "tracking":
        status_filter = {"tracking.status": "tracking"}
    else:
        status_filter = {"tracking.status": {"$in": ["complete", "tracking"]}}

    ors: List[Dict[str, Any]] = []
    ands: List[Dict[str, Any]] = [status_filter]

    if BF_FILL_HANDLE:
        ors.extend(
            [
                {"snippet.channelHandle": {"$exists": False}},
                {"source.channelHandle": {"$exists": False}},
            ]
        )

    if BF_FILL_DURATION:
        dur_missing = [
            {"snippet.durationISO": {"$exists": False}},
            {"snippet.lengthBucket": {"$exists": False}},
        ]
        if BF_SKIP_LIVE:
            ands.append({"snippet.lengthBucket": {"$ne": "live"}})
        ors.extend(dur_missing)

    if not ors:
        # No backfill requested → return a filter that matches nothing
        return {"_id": None}

    return {"$and": ands + [{"$or": ors}]}


# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
def main() -> int:
    print(">>> backfill_missing_fields starting")

    if not API_KEY:
        print("Missing YT_API_KEY", file=sys.stderr)
        return 2

    client = MongoClient(MONGO_URI)
    db = client.get_database()

    query = build_query()

    projection = {
        "_id": 1,
        "snippet.channelId": 1,
        "snippet.channelHandle": 1,
        "source.channelHandle": 1,
        "snippet.durationISO": 1,
        "snippet.durationSec": 1,
        "snippet.lengthBucket": 1,
        "tracking.status": 1,
        "snippet.publishedAt": 1,
    }

    cur = (
        db.videos.find(query, projection)
        .sort("snippet.publishedAt", -1)
        .limit(BF_LIMIT)
    )
    candidates = list(cur)

    if not candidates:
        print("[INFO] No candidate videos found for backfill.")
        return 0

    print(f"[INFO] Candidates fetched: {len(candidates)} (limit={BF_LIMIT})")
    if BF_LOG_SAMPLE and candidates:
        sample_n = min(BF_LOG_SAMPLE, len(candidates))
        print(f"[INFO] Sample {sample_n} candidates (ids only):")
        for d in candidates[:sample_n]:
            print(f"   - {d.get('_id')} | status={d.get('tracking', {}).get('status')}")

    # 1) Handles
    try:
        backfill_channel_handles(candidates, db)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_QUOTA

    # 2) Duration
    try:
        backfill_duration(candidates, db)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_QUOTA

    print("[INFO] Backfill done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
