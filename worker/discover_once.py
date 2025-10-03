
# discover_once.py — v4.1 (quota-aware + optional max pages)
# - Returns exit code 88 when YouTube quota is exhausted (quotaExceeded etc.)
# - Optional YT_MAX_PAGES to cap search pages per run (reduces quota usage)
# - All other v4 features preserved (Random Mode, SINCE_MODE, UTF‑8, .env, category filter, @handle, fallback)

from dotenv import load_dotenv
load_dotenv(override=False)

import os, sys, io, random
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo

import requests
from pymongo import MongoClient, UpdateOne

# ---- UTF-8-safe console ----
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---- Config ----
API_KEY  = os.getenv("YT_API_KEY")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")
REGION   = os.getenv("YT_REGION", "US")
QUERY    = os.getenv("YT_QUERY")
CHANNEL_ID = os.getenv("YT_CHANNEL_ID")
CHANNEL_HANDLE = os.getenv("YT_CHANNEL_HANDLE")
TOPIC_ID = os.getenv("YT_TOPIC_ID")
LOOKBACK_MINUTES = int(os.getenv("YT_LOOKBACK_MINUTES", "360"))
FILTER_CATEGORY_ID = os.getenv("YT_FILTER_CATEGORY_ID") or os.getenv("YT_VIDEO_CATEGORY_ID")

# Random mode
RANDOM_MODE = os.getenv("YT_RANDOM_MODE", "0").lower() in ("1","true","yes")
RANDOM_LOOKBACK_MINUTES = int(os.getenv("YT_RANDOM_LOOKBACK_MINUTES", "43200"))
RANDOM_WINDOW_MINUTES = int(os.getenv("YT_RANDOM_WINDOW_MINUTES", "30"))
RANDOM_REGION_POOL = [x.strip().upper() for x in os.getenv("YT_RANDOM_REGION_POOL","").split(",") if x.strip()]
RANDOM_QUERY_POOL = [x.strip() for x in os.getenv("YT_RANDOM_QUERY_POOL","").split(",") if x.strip()]

# Since-mode (non-random)
SINCE_MODE = os.getenv("YT_SINCE_MODE", "lookback")  # lookback|now|minutes|local_midnight
SINCE_MINUTES = int(os.getenv("YT_SINCE_MINUTES", "60"))
LOCAL_TZ = os.getenv("YT_LOCAL_TZ")  # e.g. America/Toronto

# Safety knobs
MAX_PAGES = os.getenv("YT_MAX_PAGES")
MAX_PAGES = int(MAX_PAGES) if (MAX_PAGES and MAX_PAGES.isdigit()) else None

# Endpoints
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

# Exit code for quota exhaustion
EXIT_QUOTA = 88


def resolve_channel_id_from_handle(handle: str) -> str:
    if not handle:
        raise ValueError("Empty handle")
    if not handle.startswith("@"):
        handle = "@" + handle
    # channels.list with forHandle
    try:
        params = {"key": API_KEY, "part": "id", "forHandle": handle}
        r = requests.get(CHANNELS_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        if items:
            return items[0]["id"]
    except requests.HTTPError:
        pass
    # fallback: search channel
    params = {"key": API_KEY, "part": "snippet", "type": "channel", "q": handle.lstrip("@"), "maxResults": 1}
    r = requests.get(SEARCH_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    items = data.get("items", [])
    if not items:
        raise ValueError(f"Cannot resolve handle: {handle}")
    return items[0]["id"]["channelId"]


def search_page(published_after_iso: str, page_token: Optional[str] = None, published_before_iso: Optional[str] = None,
                region_override: Optional[str] = None, query_override: Optional[str] = None) -> Dict[str, Any]:
    if not API_KEY:
        raise RuntimeError("Missing YT_API_KEY")
    params = {
        "key": API_KEY, "part": "snippet", "type": "video",
        "order": "date", "maxResults": 50, "publishedAfter": published_after_iso,
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
    out: Dict[str, Dict[str, Any]] = {}
    if not video_ids:
        return out
    ids = ",".join(video_ids[:50])
    params = {"key": API_KEY, "part": "snippet", "id": ids}
    r = requests.get(VIDEOS_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    for it in data.get("items", []):
        vid = it.get("id")
        if vid:
            out[vid] = it
    return out


def most_popular(region: str, category_id: str, max_results: int = 50) -> List[Dict[str, Any]]:
    params = {"key": API_KEY, "part": "snippet", "chart": "mostPopular",
              "regionCode": region, "videoCategoryId": category_id, "maxResults": max_results}
    r = requests.get(VIDEOS_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    items = [{"id": {"videoId": it["id"]}, "snippet": it["snippet"]} for it in data.get("items", [])]
    return items


def upsert_videos(items: List[Dict[str, Any]], db):
    ops = []
    now = datetime.now(timezone.utc).isoformat()
    for it in items:
        vid = it.get("id", {}).get("videoId")
        sn = it.get("snippet", {})
        if not vid or not sn:
            continue
        doc = {
            "_id": vid,
            "source": {
                "query": QUERY, "regionCode": REGION, "channelId": CHANNEL_ID,
                "channelHandle": CHANNEL_HANDLE, "topicId": TOPIC_ID,
                "filteredByCategoryId": FILTER_CATEGORY_ID, "randomMode": RANDOM_MODE,
            },
            "snippet": {
                "title": sn.get("title"),
                "publishedAt": sn.get("publishedAt"),
                "thumbnails": sn.get("thumbnails", {}),
                "channelId": sn.get("channelId"),
                "channelTitle": sn.get("channelTitle"),
            },
            "tracking": {
                "status": "tracking", "discovered_at": now, "last_polled_at": None,
                "next_poll_after": now, "poll_count": 0, "stop_reason": None
            },
            "stats_snapshots": [],
            "ml_flags": {"likely_viral": False, "viral_confirmed": False, "score": 0.0}
        }
        ops.append(UpdateOne({"_id": vid}, {"$setOnInsert": doc}, upsert=True))
    if not ops:
        return 0
    result = db.videos.bulk_write(ops, ordered=False)
    return result.upserted_count or 0


def main() -> int:
    global CHANNEL_ID, REGION, QUERY
    print(">>> discover_once v4.1 starting")
    print(f"RANDOM_MODE={RANDOM_MODE}, RANDOM_LOOKBACK_MINUTES={RANDOM_LOOKBACK_MINUTES}, RANDOM_WINDOW_MINUTES={RANDOM_WINDOW_MINUTES}")
    print(f"SINCE_MODE={SINCE_MODE}, YT_LOCAL_TZ={LOCAL_TZ}, YT_SINCE_MINUTES={SINCE_MINUTES}, YT_MAX_PAGES={MAX_PAGES}")
    if not API_KEY:
        print("Missing YT_API_KEY", file=sys.stderr); return 2

    if not CHANNEL_ID and CHANNEL_HANDLE:
        try:
            CHANNEL_ID = resolve_channel_id_from_handle(CHANNEL_HANDLE)
            print(f"Resolved handle {CHANNEL_HANDLE} -> CHANNEL_ID={CHANNEL_ID}")
        except Exception as e:
            print(f"Failed to resolve handle {CHANNEL_HANDLE}: {e}", file=sys.stderr); return 1

    client = MongoClient(MONGO_URI); db = client.get_database()
    now_utc = datetime.now(timezone.utc)

    if RANDOM_MODE:
        offset = random.randint(0, max(1, RANDOM_LOOKBACK_MINUTES))
        end = now_utc - timedelta(minutes=offset)
        start = end - timedelta(minutes=RANDOM_WINDOW_MINUTES)
        published_after = start.isoformat()
        published_before = end.isoformat()
        region_choice = random.choice(RANDOM_REGION_POOL) if RANDOM_REGION_POOL else None
        query_choice = random.choice(RANDOM_QUERY_POOL) if RANDOM_QUERY_POOL else None
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
        else:
            published_after = (now_utc - timedelta(minutes=LOOKBACK_MINUTES)).isoformat()

        published_before = None
        region_choice = None
        query_choice = None
        print(f"Deterministic slice: {published_after}..(now) | region={REGION} | query={QUERY!r} | SINCE_MODE={SINCE_MODE}")

    page_token = None
    total_found = 0
    total_upserted = 0
    page = 0
    try:
        while True:
            page += 1
            if MAX_PAGES and page > MAX_PAGES:
                print(f"Reached YT_MAX_PAGES={MAX_PAGES}, stopping to save quota.")
                break

            data = search_page(published_after, page_token, published_before_iso=published_before,
                               region_override=region_choice, query_override=query_choice)
            items = data.get("items", [])
            found = len(items)
            total_found += found

            if FILTER_CATEGORY_ID and found > 0:
                ids = [it.get("id", {}).get("videoId") for it in items if it.get("id", {}).get("videoId")]
                details = videos_details(ids)
                filtered = []
                for it in items:
                    vid = it.get("id", {}).get("videoId")
                    if not vid:
                        continue
                    cat = details.get(vid, {}).get("snippet", {}).get("categoryId")
                    if cat == FILTER_CATEGORY_ID:
                        filtered.append(it)
                print(f"[page {page}] found={found}, after category filter({FILTER_CATEGORY_ID}) -> {len(filtered)}")
                items = filtered
            else:
                print(f"[page {page}] found={found}")

            upserted = upsert_videos(items, db)
            total_upserted += upserted
            print(f"[page {page}] upserted={upserted}")
            for it in items[:5]:
                vid = it["id"]["videoId"]
                title = it["snippet"]["title"]
                published = it["snippet"]["publishedAt"]
                print(f" - {vid} | {published} | {title}")

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        if total_found == 0 and FILTER_CATEGORY_ID and (region_choice or REGION):
            print("No recent results. Falling back to mostPopular…")
            items = most_popular(region_choice or REGION, FILTER_CATEGORY_ID)
            up = upsert_videos(items, db)
            print(f"fallback mostPopular: upserted={up}")

    except requests.HTTPError as e:
        # Parse API error
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
        # Quota/rate reasons from YouTube
        quota_reasons = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}
        if status == 403 and str(reason) in quota_reasons:
            print("YouTube quota exhausted — stopping so you can update YT_API_KEY.", file=sys.stderr)
            return EXIT_QUOTA

        print("YouTube API error:", body, file=sys.stderr)
        return 2

    except Exception as e:
        print("Error:", e, file=sys.stderr)
        return 1

    print(f">>> DONE. total_found={total_found}, total_upserted={total_upserted}")
    print("Tip: open http://127.0.0.1:8000/videos?sort=discovered&order=desc&limit=10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())