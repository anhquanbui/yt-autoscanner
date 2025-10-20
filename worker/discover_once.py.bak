# worker/discover_once.py — VIDEO DISCOVERY (Near-now scan with categoryId)
# ---------------------------------------------------------------------------------
# PURPOSE:
#   Discover newly published YouTube videos (within a near-now window)
#   and insert them into MongoDB for later tracking by track_once.py.
#   It supports both deterministic time-slice (SINCE_MODE) and randomized
#   discovery (RANDOM_MODE) with region and query pools.
#
# ---------------------------------------------------------------------------------
# OPERATING MODES
#
# 1️⃣ Random Mode (Exploratory / Weighted Sampling)
#     Enabled when:  YT_RANDOM_MODE=1
#
#     Variables:
#       - YT_RANDOM_LOOKBACK_MINUTES  : how far back to pick the random slice (default: 43200 = 30 days)
#       - YT_RANDOM_WINDOW_MINUTES    : window length for that slice (default: 30)
#       - YT_RANDOM_REGION_POOL       : comma-separated region list (e.g. "US,GB,JP,VN")
#       - YT_RANDOM_QUERY_POOL        : weighted keyword list (e.g. "live:5,news:3,gaming:4,music:2")
#
#     Example:
#       YT_RANDOM_MODE=1
#       YT_RANDOM_REGION_POOL=US,GB,JP,VN
#       YT_RANDOM_QUERY_POOL=live:5,news:3,gaming:4,music:2,trailer:1
#
#     Behavior:
#       - Randomly selects a region from REGION_POOL
#       - Randomly selects a keyword based on weighted probability
#       - Picks a random time slice (e.g. 20–30 minutes window)
#       - Quota use: ~101 units per page (50 results + category lookup)
#
# 2️⃣ Since Mode (Deterministic Near-now)
#     Enabled when:  YT_RANDOM_MODE=0 or not set
#
#     Variables:
#       - YT_SINCE_MODE        : "minutes" (default) | "lookback" | "local_midnight"
#       - YT_SINCE_MINUTES     : how many minutes to look back (default: 20)
#       - YT_REGION            : fixed region (default: US)
#       - YT_QUERY             : fixed keyword (optional)
#       - YT_LOCAL_TZ          : required only for local_midnight
#
#     Example:
#       YT_SINCE_MODE=minutes
#       YT_SINCE_MINUTES=10
#       YT_REGION=US
#       YT_QUERY=live
#
# ---------------------------------------------------------------------------------
# QUOTA SAFETY
#   - YT_MAX_PAGES limits the number of search pages (default: 1).
#     Each page = 50 results (100 quota units for search + 1 for category lookup).
#   - Recommended for scheduled runs: 1–3 pages per call.
#
# ---------------------------------------------------------------------------------
# DATABASE STRUCTURE
#   Each discovered video is inserted (upsert) into MongoDB:
#     {
#       _id: <videoId>,
#       source: { query, regionCode, randomMode, filteredByCategoryId },
#       snippet: { title, publishedAt, thumbnails, channelId, channelTitle, categoryId },
#       tracking: { status, discovered_at, next_poll_after, poll_count },
#       stats_snapshots: [],
#       ml_flags: { likely_viral, viral_confirmed, score }
#     }
#
# ---------------------------------------------------------------------------------
# EXIT CODES
#   0  = success
#   88 = YouTube quota exhausted (quotaExceeded / rateLimitExceeded)
#   2  = missing API key
#   1  = other errors
#
# ---------------------------------------------------------------------------------
# RECOMMENDED ENV CONFIG (example .env)
#
#   YT_API_KEY=<your_youtube_api_key>
#   MONGO_URI=mongodb://localhost:27017/ytscan
#
#   # --- Random mode (global weighted pool) ---
#   YT_RANDOM_MODE=1
#   YT_RANDOM_LOOKBACK_MINUTES=43200
#   YT_RANDOM_WINDOW_MINUTES=30
#   YT_RANDOM_REGION_POOL=US,GB,JP,VN,KR,IN,BR,CA,DE,FR
#   YT_RANDOM_QUERY_POOL=live:5,news:3,gaming:4,music:3,highlights:4,shorts:2,trailer:2,stream:3,review:3,performance:2
#
#   # --- Quota & interval ---
#   YT_MAX_PAGES=1
#   DISCOVER_INTERVAL_SECONDS=1800
#
# ---------------------------------------------------------------------------------
# NOTE:
#   - The script intentionally avoids collecting statistics or full video details
#     to keep discovery lightweight and cheap in quota.
#   - Detailed updates (stats, engagement, ML flags) are handled later by track_once.py.
#
# ---------------------------------------------------------------------------------

from __future__ import annotations

import os, sys, io, random
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple

import requests
from pymongo import MongoClient, UpdateOne
from dotenv import load_dotenv

# ----- Console UTF-8 (Windows-safe) -----
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

load_dotenv(override=False)

# ----- Config -----
API_KEY   = os.getenv('YT_API_KEY')
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/ytscan')

REGION = os.getenv('YT_REGION', 'US')
QUERY  = os.getenv('YT_QUERY')  # optional fallback keyword

# RANDOM picker (region/query) — near-now window always
RANDOM_PICK        = os.getenv('YT_RANDOM_PICK', '0').lower() in ('1','true','yes')
RANDOM_REGION_POOL = [x.strip().upper() for x in os.getenv('YT_RANDOM_REGION_POOL', '').split(',') if x.strip()]

# Weighted keyword pools (global or region-specific)
# Examples:
#   YT_RANDOM_QUERY_POOL="live:5, news:3, music:1"
#   YT_RANDOM_QUERY_POOL_US="nfl:4, nba:3, mlb:2, highlights:5"
#   YT_RANDOM_QUERY_POOL_JP="vtuber:5, anime:4, jpop:3"
GLOBAL_QUERY_POOL = os.getenv('YT_RANDOM_QUERY_POOL', '')

# Near-now window (minutes)
SINCE_MINUTES = int(os.getenv('YT_SINCE_MINUTES', '20'))  # default 20

# Quota safety: cap number of pages (each page=50 results)
MAX_PAGES_ENV = os.getenv('YT_MAX_PAGES', '1')  # default 1 page
try:
    MAX_PAGES = int(MAX_PAGES_ENV)
except Exception:
    MAX_PAGES = 1

# API endpoints
SEARCH_URL = 'https://www.googleapis.com/youtube/v3/search'
VIDEOS_URL = 'https://www.googleapis.com/youtube/v3/videos'

EXIT_QUOTA = 88


def parse_weighted_pool(val: str) -> Tuple[List[str], List[float]]:
    """
    Parse a weighted CSV string into (choices, weights).
    - Input format: "termA:5, termB:2, term C:1"  (weight defaults to 1 if omitted)
    - Trims whitespace; ignores empty items and zero/negative weights.
    """
    if not val:
        return [], []
    choices: List[str] = []
    weights: List[float] = []
    for raw in val.split(','):
        item = raw.strip()
        if not item:
            continue
        if ':' in item:
            term, w = item.split(':', 1)
            term = term.strip()
            try:
                weight = float(w.strip())
            except Exception:
                weight = 1.0
        else:
            term = item
            weight = 1.0
        if term and weight > 0:
            choices.append(term)
            weights.append(weight)
    return choices, weights


def pick_query_for_region(region_code: str) -> Optional[str]:
    """
    Choose a keyword for the given region using region-specific pool if present,
    else fall back to global pool, else None (which means 'no q' param).
    Region-specific env name: YT_RANDOM_QUERY_POOL_<REGION>, e.g., YT_RANDOM_QUERY_POOL_US
    """
    env_name = f'YT_RANDOM_QUERY_POOL_{region_code.upper()}'
    val = os.getenv(env_name, '').strip()
    if not val:
        val = GLOBAL_QUERY_POOL.strip()
    choices, weights = parse_weighted_pool(val)
    if choices:
        try:
            return random.choices(choices, weights=weights, k=1)[0]
        except Exception:
            return random.choice(choices)
    return None


def search_page(published_after_iso: str, region_code: str, query_str: Optional[str], page_token: Optional[str] = None) -> Dict[str, Any]:
    if not API_KEY:
        raise RuntimeError('Missing YT_API_KEY')
    params = {
        'key': API_KEY,
        'part': 'snippet',
        'type': 'video',
        'order': 'date',
        'maxResults': 50,
        'regionCode': region_code,
        'publishedAfter': published_after_iso,
    }
    if query_str:
        params['q'] = query_str
    if page_token:
        params['pageToken'] = page_token
    r = requests.get(SEARCH_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def videos_snippet(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Return {videoId: snippet} to enrich categoryId (cheap: 1 quota per 50 IDs)."""
    out: Dict[str, Dict[str, Any]] = {}
    if not video_ids:
        return out
    params = {'key': API_KEY, 'part': 'snippet', 'id': ','.join(video_ids[:50])}
    r = requests.get(VIDEOS_URL, params=params, timeout=30)
    r.raise_for_status()
    for it in r.json().get('items', []):
        vid = it.get('id')
        if vid:
            out[vid] = it.get('snippet', {})
    return out


def upsert_minimal(items: List[Dict[str, Any]], db, region_used: str, query_used: Optional[str]) -> int:
    """Insert minimal video docs; tracker will enrich/track later."""
    ops = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for it in items:
        vid = it.get('id', {}).get('videoId')
        sn  = it.get('snippet', {}) or {}
        if not vid or not sn:
            continue
        doc = {
            '_id': vid,
            'source': {
                'query': query_used,
                'regionCode': region_used,
                'randomMode': bool(RANDOM_PICK),
            },
            'snippet': {
                'title': sn.get('title'),
                'publishedAt': sn.get('publishedAt'),
                'thumbnails': sn.get('thumbnails', {}),
                'channelId': sn.get('channelId'),
                'channelTitle': sn.get('channelTitle'),
                'categoryId': sn.get('categoryId'),  # set by enrichment below
            },
            'tracking': {
                'status': 'tracking',
                'discovered_at': now_iso,
                'last_polled_at': None,
                'next_poll_after': now_iso,
                'poll_count': 0,
                'stop_reason': None,
            },
            'stats_snapshots': [],
            'ml_flags': {'likely_viral': False, 'viral_confirmed': False, 'score': 0.0},
        }
        ops.append(UpdateOne({'_id': vid}, {'$setOnInsert': doc}, upsert=True))
    if not ops:
        return 0
    res = db.videos.bulk_write(ops, ordered=False)
    return int(res.upserted_count or 0)


def main() -> int:
    print('>>> discover_once SCAN-ONLY (near-now + categoryId + RANDOM weighted pick) starting')
    if not API_KEY:
        print('Missing YT_API_KEY', file=sys.stderr)
        return 2

    # region pick
    region_used = (random.choice(RANDOM_REGION_POOL) if RANDOM_PICK and RANDOM_REGION_POOL else REGION)

    # query pick (region-specific weighted)
    query_used = None
    if RANDOM_PICK:
        query_used = pick_query_for_region(region_used)
    if not query_used:
        query_used = QUERY  # may be None/empty → omit q

    client = MongoClient(MONGO_URI)
    db = client.get_database()
    now = datetime.now(timezone.utc)
    published_after = (now - timedelta(minutes=SINCE_MINUTES)).isoformat()
    print(f'Near-now slice: {published_after}..(now) | region={region_used} | query={query_used!r} | random={RANDOM_PICK}')

    page_token = None
    pages = 0
    total_found = 0
    total_upserted = 0

    try:
        while True:
            if pages >= MAX_PAGES:
                print(f'Reached YT_MAX_PAGES={MAX_PAGES}, stop.')
                break
            pages += 1

            data = search_page(published_after, region_used, query_used, page_token)
            items = data.get('items', [])
            found = len(items)
            total_found += found

            # Enrich categoryId for ALL items (1 quota per 50)
            if found > 0:
                ids = [it.get('id', {}).get('videoId') for it in items if it.get('id', {}).get('videoId')]
                sn_map = videos_snippet(ids)
                enriched = 0
                for it in items:
                    vid = it.get('id', {}).get('videoId')
                    sn = it.get('snippet', {}) or {}
                    cate = (sn_map.get(vid) or {}).get('categoryId')
                    if cate:
                        sn['categoryId'] = cate
                        it['snippet'] = sn
                        enriched += 1
                print(f'[page {pages}] found={found}, enriched_category={enriched}')

            up = upsert_minimal(items, db, region_used, query_used)
            total_upserted += up

            for it in items[:5]:
                vid = it.get('id', {}).get('videoId')
                sn  = it.get('snippet', {})
                print(f' - {vid} | {sn.get("publishedAt")} | cate={sn.get("categoryId")} | {sn.get("title")}')

            page_token = data.get('nextPageToken')
            if not page_token:
                break

        print(f'>>> DONE. pages={pages}, total_found={total_found}, total_upserted={total_upserted}')
        return 0

    except requests.HTTPError as e:
        # Detect quota exhaustion
        try:
            body = e.response.json()
        except Exception:
            body = {'error': str(e)}
        reason = None
        err = body.get('error') if isinstance(body, dict) else None
        if isinstance(err, dict):
            errs = err.get('errors') or []
            if isinstance(errs, list) and errs:
                reason = errs[0].get('reason')
            reason = reason or err.get('status') or err.get('message')
        if getattr(e.response, 'status_code', None) == 403 and str(reason) in {'quotaExceeded','dailyLimitExceeded','rateLimitExceeded','userRateLimitExceeded'}:
            print('YouTube quota exhausted — update YT_API_KEY.', file=sys.stderr)
            return EXIT_QUOTA
        print('YouTube API error:', body, file=sys.stderr)
        return 1
    except Exception as e:
        print('Error:', e, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
