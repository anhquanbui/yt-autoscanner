# worker/discover_once.py  — SIMPLE SCAN & INSERT (no statistics snapshot)
# ---------------------------------------------------------------------------------
# What it does:
#   - Query YouTube Search API for newly published videos.
#   - (Optional) Filter by category (via a cheap videos.list?part=snippet call).
#   - Upsert each video into MongoDB with tracking.status="tracking" and
#     tracking.next_poll_after=now (so a separate tracker can handle re-polls).
#   - No statistics snapshot here (views/likes/comments are collected by track_once.py).
#
# Quota notes:
#   - search.list costs 100 units per page (maxResults=50).
#   - videos.list?part=snippet for category check costs 1 unit per request (up to 50 IDs).
#   - This script is intentionally minimal to keep "discover" cheap and simple.
#
# Exit codes:
#   - 0  : success
#   - 88 : YouTube quota exhausted (quotaExceeded/dailyLimitExceeded/...)
#   - 1/2: other errors
#
# Environment (secrets kept in .env; runtime config via ENV):
#   Required:
#     - YT_API_KEY
#     - MONGO_URI (e.g., mongodb://localhost:27017/ytscan)
#   Optional scope filters:
#     - YT_REGION (default: US), YT_QUERY
#     - YT_CHANNEL_ID or YT_CHANNEL_HANDLE (e.g. @SomeChannel)
#     - YT_TOPIC_ID
#     - YT_FILTER_CATEGORY_ID (== YT_VIDEO_CATEGORY_ID)
#   Time window (non-random mode):
#     - YT_SINCE_MODE   : now | minutes | lookback | local_midnight   (default: minutes)
#     - YT_SINCE_MINUTES: 10..N (default: 60)
#     - YT_LOOKBACK_MINUTES (default: 360)
#     - YT_LOCAL_TZ     : e.g. America/Toronto (only for local_midnight)
#     - YT_MAX_PAGES    : cap search pages per run (int; reduce quota)
#   Random mode (optional exploratory scanning, expensive if abused):
#     - YT_RANDOM_MODE=1
#     - YT_RANDOM_LOOKBACK_MINUTES (default: 43200, i.e. 30 days pool)
#     - YT_RANDOM_WINDOW_MINUTES   (default: 30)
#     - YT_RANDOM_REGION_POOL      (e.g. "US,GB,JP,VN")
#     - YT_RANDOM_QUERY_POOL       (e.g. "gaming,stream,highlights")
#
# Tips:
#   - Keep YT_MAX_PAGES low (1–2) for periodic cron runs.
#   - Let track_once.py do the re-polls (1h .. 24h milestones), batching 50 IDs/request.
#   - Add Mongo indexes:
#       db.videos.createIndex({ "snippet.publishedAt": -1 })
#       db.videos.createIndex({ "tracking.status": 1, "tracking.next_poll_after": 1 })
#       db.videos.createIndex({ "source.regionCode": 1 })
#
from __future__ import annotations

import os, sys, io, random
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo

import requests
from pymongo import MongoClient, UpdateOne
from dotenv import load_dotenv
from dateutil import parser as dtp

# Ensure UTF‑8 console (Windows PowerShell can throw charmap errors otherwise)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Load .env but DO NOT override existing ENV (so server/cron ENV wins)
load_dotenv(override=False)

# ---- Config (read once at import) ----
API_KEY   = os.getenv("YT_API_KEY")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")

REGION         = os.getenv("YT_REGION", "US")
QUERY          = os.getenv("YT_QUERY")
CHANNEL_ID     = os.getenv("YT_CHANNEL_ID")
CHANNEL_HANDLE = os.getenv("YT_CHANNEL_HANDLE")
TOPIC_ID       = os.getenv("YT_TOPIC_ID")

# Optional category filter (requires an extra cheap videos.list call)
FILTER_CATEGORY_ID = os.getenv("YT_FILTER_CATEGORY_ID") or os.getenv("YT_VIDEO_CATEGORY_ID")

# Random mode knobs
RANDOM_MODE              = os.getenv("YT_RANDOM_MODE", "0").lower() in ("1","true","yes")
RANDOM_LOOKBACK_MINUTES  = int(os.getenv("YT_RANDOM_LOOKBACK_MINUTES", "43200"))
RANDOM_WINDOW_MINUTES    = int(os.getenv("YT_RANDOM_WINDOW_MINUTES", "30"))
RANDOM_REGION_POOL       = [x.strip().upper() for x in os.getenv("YT_RANDOM_REGION_POOL","").split(",") if x.strip()]
RANDOM_QUERY_POOL        = [x.strip() for x in os.getenv("YT_RANDOM_QUERY_POOL","").split(",") if x.strip()]

# Deterministic (non-random) time window
SINCE_MODE        = os.getenv("YT_SINCE_MODE", "minutes")  # now|minutes|lookback|local_midnight
SINCE_MINUTES     = int(os.getenv("YT_SINCE_MINUTES", "60"))
LOOKBACK_MINUTES  = int(os.getenv("YT_LOOKBACK_MINUTES", "360"))
LOCAL_TZ          = os.getenv("YT_LOCAL_TZ")               # e.g. America/Toronto

# Safety: cap number of pages (each page=50 results, 100 quota units)
MAX_PAGES = os.getenv("YT_MAX_PAGES")
MAX_PAGES = int(MAX_PAGES) if (MAX_PAGES and MAX_PAGES.isdigit()) else None

# API endpoints
SEARCH_URL   = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL   = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

# Exit code for quota exhaustion
EXIT_QUOTA = 88


def resolve_channel_id_from_handle(handle: str) -> str:
    """Resolve a @handle to channelId using channels.list(forHandle), then fallback to search."""
    if not handle:
        raise ValueError("Empty handle")
    if not handle.startswith("@"):
        handle = "@" + handle
    # Try channels.list with forHandle
    try:
        params = {"key": API_KEY, "part": "id", "forHandle": handle}
        r = requests.get(CHANNELS_URL, params=params, timeout=30)
        r.raise_for_status()
        items = r.json().get("items", [])
        if items:
            return items[0]["id"]
    except requests.HTTPError:
        pass
    # Fallback: search channel by text
    params = {"key": API_KEY, "part": "snippet", "type": "channel", "q": handle.lstrip("@"), "maxResults": 1}
    r = requests.get(SEARCH_URL, params=params, timeout=30)
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        raise ValueError(f"Cannot resolve handle: {handle}")
    return items[0]["id"]["channelId"]


def search_page(published_after_iso: str,
                page_token: Optional[str] = None,
                published_before_iso: Optional[str] = None,
                region_override: Optional[str] = None,
                query_override: Optional[str] = None) -> Dict[str, Any]:
    """Call search.list for a single page (50 results)."""
    if not API_KEY:
        raise RuntimeError("Missing YT_API_KEY")
    params = {
        "key": API_KEY,
        "part": "snippet",
        "type": "video",
        "order": "date",
        "maxResults": 50,
        "publishedAfter": published_after_iso,
        "regionCode": region_override or REGION,
    }
    if published_before_iso:
        params["publishedBefore"] = published_before_iso
    if query_override is not None:
        if query_override:
            params["q"] = query_override
    elif QUERY:
        params["q"] = QUERY
    if CHANNEL_ID:
        params["channelId"] = CHANNEL_ID
    if TOPIC_ID:
        params["topicId"] = TOPIC_ID
    if page_token:
        params["pageToken"] = page_token

    r = requests.get(SEARCH_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def videos_details(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch snippet for multiple IDs to read snippet.categoryId for filtering (cheap: 1 unit)."""
    out: Dict[str, Dict[str, Any]] = {}
    if not video_ids:
        return out
    params = {"key": API_KEY, "part": "snippet", "id": ",".join(video_ids[:50])}
    r = requests.get(VIDEOS_URL, params=params, timeout=30)
    r.raise_for_status()
    for it in r.json().get("items", []):
        vid = it.get("id")
        if vid:
            out[vid] = it
    return out


def upsert_videos(items: List[Dict[str, Any]], db, region_used, query_used) -> int:
    """Insert-only (setOnInsert). No statistics snapshot; tracker will handle later."""
    ops = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for it in items:
        vid = it.get("id", {}).get("videoId")
        sn  = it.get("snippet", {})
        if not vid or not sn:
            continue
        doc = {
            "_id": vid,
            "source": {
                "query": query_used,
                "regionCode": region_used,
                "channelId": CHANNEL_ID,
                "channelHandle": CHANNEL_HANDLE,
                "topicId": TOPIC_ID,
                "filteredByCategoryId": FILTER_CATEGORY_ID,
                "randomMode": RANDOM_MODE,
            },
            "snippet": {
                "title": sn.get("title"),
                "publishedAt": sn.get("publishedAt"),
                "thumbnails": sn.get("thumbnails", {}),
                "channelId": sn.get("channelId"),
                "channelTitle": sn.get("channelTitle"),
                # If we already looked up categoryId via videos_details, keep it
                "categoryId": sn.get("categoryId"),
            },
            "tracking": {
                "status": "tracking",
                "discovered_at": now_iso,
                "last_polled_at": None,
                # Start tracking immediately; track_once.py will align to publish-based milestones
                "next_poll_after": now_iso,
                "poll_count": 0,
                "stop_reason": None
            },
            "stats_snapshots": [],
            "ml_flags": {"likely_viral": False, "viral_confirmed": False, "score": 0.0}
        }
        ops.append(UpdateOne({"_id": vid}, {"$setOnInsert": doc}, upsert=True))

    if not ops:
        return 0
    res = db.videos.bulk_write(ops, ordered=False)
    return int(res.upserted_count or 0)


def main() -> int:
    print(">>> discover_once SIMPLE starting")
    if not API_KEY:
        print("Missing YT_API_KEY", file=sys.stderr)
        return 2

    # Resolve @handle to channelId if provided
    global CHANNEL_ID
    if not CHANNEL_ID and CHANNEL_HANDLE:
        try:
            CHANNEL_ID = resolve_channel_id_from_handle(CHANNEL_HANDLE)
            print(f"Resolved handle {CHANNEL_HANDLE} -> CHANNEL_ID={CHANNEL_ID}")
        except Exception as e:
            print(f"Failed to resolve handle {CHANNEL_HANDLE}: {e}", file=sys.stderr)
            return 1

    client = MongoClient(MONGO_URI)
    db = client.get_database()
    now_utc = datetime.now(timezone.utc)

    # Build the time slice
    region_choice = None
    query_choice  = None
    published_before = None

    region_used = REGION
    query_used = QUERY
    if RANDOM_MODE:
        offset_min = 0 if RANDOM_LOOKBACK_MINUTES <= 0 else random.randint(0, RANDOM_LOOKBACK_MINUTES)
        end = now_utc - timedelta(minutes=offset_min)
        start = end - timedelta(minutes=RANDOM_WINDOW_MINUTES)
        published_after = start.isoformat()
        published_before = end.isoformat()
        region_choice = random.choice(RANDOM_REGION_POOL) if RANDOM_REGION_POOL else None
        query_choice  = random.choice(RANDOM_QUERY_POOL)  if RANDOM_QUERY_POOL  else None
        print(f"Random slice: {published_after}..{published_before} | region={region_choice or REGION} | query={query_choice!r}")
    else:
        if   SINCE_MODE == "now":
            published_after = now_utc.isoformat()
        elif SINCE_MODE == "minutes":
            published_after = (now_utc - timedelta(minutes=SINCE_MINUTES)).isoformat()
        elif SINCE_MODE == "local_midnight" and LOCAL_TZ:
            now_local = datetime.now(ZoneInfo(LOCAL_TZ))
            midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            published_after = midnight_local.astimezone(timezone.utc).isoformat()
        else:  # lookback (default fallback)
            published_after = (now_utc - timedelta(minutes=LOOKBACK_MINUTES)).isoformat()

        print(f"Deterministic slice: {published_after}..(now) | region={REGION} | query={QUERY!r} | SINCE_MODE={SINCE_MODE}")
    region_used = (region_choice or REGION)
    query_used = (query_choice if query_choice is not None else QUERY)

    # Iterate pages (each page = 50 results)
    page_token = None
    total_found = 0
    total_upserted = 0
    pages = 0

    try:
        while True:
            if MAX_PAGES and pages >= MAX_PAGES:
                print(f"Reached YT_MAX_PAGES={MAX_PAGES}, stopping to save quota.")
                break

            pages += 1
            data = search_page(
                published_after_iso=published_after,
                page_token=page_token,
                published_before_iso=published_before,
                region_override=region_choice,
                query_override=query_choice,
            )
            items = data.get("items", [])
            found = len(items)
            total_found += found

            # Optional: filter by category using videos.list (cheap 1 unit per 50 IDs)
            if FILTER_CATEGORY_ID and found > 0:
                ids = [it.get("id", {}).get("videoId") for it in items if it.get("id", {}).get("videoId")]
                det = videos_details(ids)
                filtered = []
                for it in items:
                    vid = it.get("id", {}).get("videoId")
                    if not vid:
                        continue
                    cat = det.get(vid, {}).get("snippet", {}).get("categoryId")
                    if cat == FILTER_CATEGORY_ID:
                        it.setdefault("snippet", {})["categoryId"] = cat  # keep for Mongo
                        filtered.append(it)
                print(f"[page {pages}] found={found}, category({FILTER_CATEGORY_ID}) -> {len(filtered)}")
                items = filtered
            else:
                print(f"[page {pages}] found={found}")

            up = upsert_videos(items, db, region_used, query_used)
            total_upserted += up

            # Print a few sample rows for visibility
            for it in items[:5]:
                vid = it.get("id", {}).get("videoId")
                sn  = it.get("snippet", {})
                print(f" - {vid} | {sn.get('publishedAt')} | {sn.get('title')}")

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        print(f">>> DONE. pages={pages}, total_found={total_found}, total_upserted={total_upserted}")
        print("Tip: open http://127.0.0.1:8000/videos?limit=10 to verify.")
        return 0

    except requests.HTTPError as e:
        # Parse API error to detect quota exhaustion
        status = getattr(e.response, "status_code", None)
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
        if status == 403 and str(reason) in quota_reasons:
            print("YouTube quota exhausted — stopping so you can update YT_API_KEY.", file=sys.stderr)
            return EXIT_QUOTA

        print("YouTube API error:", body, file=sys.stderr)
        return 2

    except Exception as e:
        print("Error:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())