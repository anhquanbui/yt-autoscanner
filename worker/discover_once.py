
# discover_once.py
# v2: Supports @handle -> channelId, category filtering via videos.list,
#     and adjustable lookback window. Falls back to mostPopular if search returns 0.
#
# Env vars:
#   YT_API_KEY (required)
#   MONGO_URI (default: mongodb://localhost:27017/ytscan)
#   YT_REGION (default: US)
#   YT_QUERY (optional)
#   YT_CHANNEL_ID (optional)
#   YT_CHANNEL_HANDLE (optional, e.g., "@MrBeastGaming")
#   YT_TOPIC_ID (optional, e.g., "/m/0bzvm2" for Gaming)
#   YT_LOOKBACK_MINUTES (default: 360)
#   YT_FILTER_CATEGORY_ID (optional, e.g., "20" for Gaming). Alias: YT_VIDEO_CATEGORY_ID
#
# Usage (PowerShell example):
#   $env:YT_API_KEY="..."
#   $env:MONGO_URI="mongodb://localhost:27017/ytscan"
#   $env:YT_REGION="US"
#   $env:YT_CHANNEL_HANDLE="@SomeChannel"
#   $env:YT_FILTER_CATEGORY_ID="20"
#   $env:YT_LOOKBACK_MINUTES="1440"
#   Remove-Item Env:YT_QUERY -ErrorAction SilentlyContinue
#   python .\worker\discover_once.py

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

import requests
from pymongo import MongoClient, UpdateOne

API_KEY  = os.getenv("YT_API_KEY")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")
REGION   = os.getenv("YT_REGION", "US")
QUERY    = os.getenv("YT_QUERY")
CHANNEL_ID = os.getenv("YT_CHANNEL_ID")
CHANNEL_HANDLE = os.getenv("YT_CHANNEL_HANDLE")
TOPIC_ID = os.getenv("YT_TOPIC_ID")
LOOKBACK_MINUTES = int(os.getenv("YT_LOOKBACK_MINUTES", "360"))
FILTER_CATEGORY_ID = os.getenv("YT_FILTER_CATEGORY_ID") or os.getenv("YT_VIDEO_CATEGORY_ID")

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


def resolve_channel_id_from_handle(handle: str) -> str:
    """Resolve @handle -> UC... channelId via channels.list; fallback to search if needed."""
    if not handle:
        raise ValueError("Empty handle")
    if not handle.startswith("@"):
        handle = "@" + handle

    # Try channels.list with forHandle (newer param)
    try:
        params = {"key": API_KEY, "part": "id", "forHandle": handle}
        r = requests.get(CHANNELS_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        if items:
            return items[0]["id"]
    except requests.HTTPError:
        # continue to fallback
        pass

    # Fallback: search for the channel by text, type=channel
    params = {
        "key": API_KEY, "part": "snippet", "type": "channel",
        "q": handle.lstrip("@"), "maxResults": 1,
    }
    r = requests.get(SEARCH_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    items = data.get("items", [])
    if not items:
        raise ValueError(f"Cannot resolve handle: {handle}")
    return items[0]["id"]["channelId"]


def search_page(published_after_iso: str, page_token: Optional[str] = None) -> Dict[str, Any]:
    if not API_KEY:
        raise RuntimeError("Missing YT_API_KEY")
    params = {
        "key": API_KEY, "part": "snippet", "type": "video",
        "order": "date", "maxResults": 50, "publishedAfter": published_after_iso,
        "regionCode": REGION,
    }
    if QUERY:
        params["q"] = QUERY
    if CHANNEL_ID:
        params["channelId"] = CHANNEL_ID
    if TOPIC_ID:
        params["topicId"] = TOPIC_ID  # optional topic filter
    if page_token:
        params["pageToken"] = page_token
    r = requests.get(SEARCH_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def videos_details(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Return mapping id -> video resource with 'snippet' (for categoryId)."""
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
    """Fallback to trending list to ensure we have some data. Normalize to search-like items."""
    params = {
        "key": API_KEY, "part": "snippet", "chart": "mostPopular",
        "regionCode": region, "videoCategoryId": category_id, "maxResults": max_results
    }
    r = requests.get(VIDEOS_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    items = []
    for it in data.get("items", []):
        items.append({"id": {"videoId": it["id"]}, "snippet": it["snippet"]})
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
                "filteredByCategoryId": FILTER_CATEGORY_ID
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
    if ops:
        result = db.videos.bulk_write(ops, ordered=False)
        return result.upserted_count or 0
    return 0


def main() -> int:
    global CHANNEL_ID
    print(">>> discover_once starting")
    print(f"MONGO_URI            = {MONGO_URI}")
    print(f"REGION               = {REGION}")
    print(f"QUERY                = {QUERY!r}")
    print(f"CHANNEL_ID           = {CHANNEL_ID!r}")
    print(f"CHANNEL_HANDLE       = {CHANNEL_HANDLE!r}")
    print(f"TOPIC_ID             = {TOPIC_ID!r}")
    print(f"LOOKBACK_MINUTES     = {LOOKBACK_MINUTES}")
    print(f"FILTER_CATEGORY_ID   = {FILTER_CATEGORY_ID!r}")

    if not API_KEY:
        print("Missing YT_API_KEY", file=sys.stderr)
        return 2

    # Resolve handle -> channelId if needed
    if not CHANNEL_ID and CHANNEL_HANDLE:
        try:
            CHANNEL_ID = resolve_channel_id_from_handle(CHANNEL_HANDLE)
            print(f"Resolved handle {CHANNEL_HANDLE} -> CHANNEL_ID={CHANNEL_ID}")
        except Exception as e:
            print(f"Failed to resolve handle {CHANNEL_HANDLE}: {e}", file=sys.stderr)
            return 1

    client = MongoClient(MONGO_URI)
    db = client.get_database()

    published_after = (datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES)).isoformat()

    page_token = None
    total_found = 0
    total_upserted = 0
    page = 0
    try:
        while True:
            page += 1
            data = search_page(published_after, page_token)
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

            for it in items[:5]:
                vid = it["id"]["videoId"]
                title = it["snippet"]["title"]
                published = it["snippet"]["publishedAt"]
                print(f" - {vid} | {published} | {title}")

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        # Fallback to mostPopular if we found nothing but want a category
        if total_found == 0 and FILTER_CATEGORY_ID and REGION:
            print("No recent results. Falling back to mostPopular…")
            items = most_popular(REGION, FILTER_CATEGORY_ID)
            up = upsert_videos(items, db)
            print(f"fallback mostPopular: upserted={up}")

    except requests.HTTPError as e:
        try:
            body = e.response.json()
        except Exception:
            body = {"error": str(e)}
        print("YouTube API error:", body, file=sys.stderr)
        return 2
    except Exception as e:
        print("Error:", e, file=sys.stderr)
        return 1

    print(f">>> DONE. total_found={total_found}, total_upserted={total_upserted}")
    print("Tip: open http://127.0.0.1:8000/videos?limit=10 to verify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
