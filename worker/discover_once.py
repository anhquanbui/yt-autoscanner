# discover_once.py (v2): recent videos via search.list, optional filter by category via videos.list
import os, sys
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
import requests
from pymongo import MongoClient, UpdateOne

API_KEY  = os.getenv('YT_API_KEY')
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/ytscan')
REGION   = os.getenv('YT_REGION', 'US')
QUERY    = os.getenv('YT_QUERY')
CHANNEL_ID = os.getenv('YT_CHANNEL_ID')
TOPIC_ID = os.getenv('YT_TOPIC_ID')
LOOKBACK_MINUTES = int(os.getenv('YT_LOOKBACK_MINUTES', '360'))
FILTER_CATEGORY_ID = os.getenv('YT_FILTER_CATEGORY_ID') or os.getenv('YT_VIDEO_CATEGORY_ID')

SEARCH_URL = 'https://www.googleapis.com/youtube/v3/search'
VIDEOS_URL = 'https://www.googleapis.com/youtube/v3/videos'

def search_page(published_after_iso: str, page_token: Optional[str] = None) -> Dict[str, Any]:
    if not API_KEY:
        raise RuntimeError('Missing YT_API_KEY')
    params = {
        'key': API_KEY, 'part': 'snippet', 'type': 'video',
        'order': 'date', 'maxResults': 50, 'publishedAfter': published_after_iso,
        'regionCode': REGION,
    }
    if QUERY:
        params['q'] = QUERY
    if CHANNEL_ID:
        params['channelId'] = CHANNEL_ID
    if TOPIC_ID:
        params['topicId'] = TOPIC_ID
    if page_token:
        params['pageToken'] = page_token
    r = requests.get(SEARCH_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def videos_details(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not video_ids:
        return out
    ids = ','.join(video_ids[:50])
    params = {'key': API_KEY, 'part': 'snippet', 'id': ids}
    r = requests.get(VIDEOS_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    for it in data.get('items', []):
        vid = it.get('id')
        if vid:
            out[vid] = it
    return out

def upsert_videos(items: List[Dict[str, Any]], db):
    ops = []
    now = datetime.now(timezone.utc).isoformat()
    for it in items:
        vid = it.get('id', {}).get('videoId')
        sn = it.get('snippet', {})
        if not vid or not sn:
            continue
        doc = {
            '_id': vid,
            'source': {
                'query': QUERY, 'regionCode': REGION, 'channelId': CHANNEL_ID,
                'topicId': TOPIC_ID, 'filteredByCategoryId': FILTER_CATEGORY_ID
            },
            'snippet': {
                'title': sn.get('title'),
                'publishedAt': sn.get('publishedAt'),
                'thumbnails': sn.get('thumbnails', {}),
                'channelId': sn.get('channelId'),
                'channelTitle': sn.get('channelTitle'),
            },
            'tracking': {
                'status': 'tracking', 'discovered_at': now, 'last_polled_at': None,
                'next_poll_after': now, 'poll_count': 0, 'stop_reason': None
            },
            'stats_snapshots': [],
            'ml_flags': {'likely_viral': False, 'viral_confirmed': False, 'score': 0.0}
        }
        ops.append(UpdateOne({'_id': vid}, {'$setOnInsert': doc}, upsert=True))
    if ops:
        result = db.videos.bulk_write(ops, ordered=False)
        return result.upserted_count or 0
    return 0

def main() -> int:
    print('>>> discover_once v2 starting')
    print(f'MONGO_URI            = {MONGO_URI}')
    print(f'REGION               = {REGION}')
    print(f'QUERY                = {QUERY!r}')
    print(f'CHANNEL_ID           = {CHANNEL_ID!r}')
    print(f'TOPIC_ID             = {TOPIC_ID!r}')
    print(f'LOOKBACK_MINUTES     = {LOOKBACK_MINUTES}')
    print(f'FILTER_CATEGORY_ID   = {FILTER_CATEGORY_ID!r}')

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
            items = data.get('items', [])
            found = len(items)
            total_found += found
            if FILTER_CATEGORY_ID:
                ids = [it.get('id', {}).get('videoId') for it in items if it.get('id', {}).get('videoId')]
                details = videos_details(ids)
                filtered = []
                for it in items:
                    vid = it.get('id', {}).get('videoId')
                    if not vid: continue
                    cat = details.get(vid, {}).get('snippet', {}).get('categoryId')
                    if cat == FILTER_CATEGORY_ID:
                        filtered.append(it)
                print(f'[page {page}] found={found}, after category filter({FILTER_CATEGORY_ID}) -> {len(filtered)}')
                items = filtered
            upserted = upsert_videos(items, db)
            total_upserted += upserted
            for it in items[:5]:
                vid = it['id']['videoId']
                title = it['snippet']['title']
                published = it['snippet']['publishedAt']
                print(f' - {vid} | {published} | {title}')
            page_token = data.get('nextPageToken')
            if not page_token:
                break
    except requests.HTTPError as e:
        try:
            body = e.response.json()
        except Exception:
            body = {'error': str(e)}
        print('YouTube API error:', body, file=sys.stderr)
        return 2
    except Exception as e:
        print('Error:', e, file=sys.stderr)
        return 1
    print(f'>>> DONE. total_found={total_found}, total_upserted={total_upserted}')
    print('Tip: open http://127.0.0.1:8000/videos?limit=10 to verify.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
