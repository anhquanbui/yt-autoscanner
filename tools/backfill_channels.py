#!/usr/bin/env python3
"""
Backfill channel metadata & lightweight features (v2, renamed to backfill_channels.py)

Goals
-----
- Default: scan ONLY channels referenced by recent `videos` docs
- Optional: --scan-all-channels to refresh the entire `channels` collection
- Quota-aware YouTube API calls, compact responses via `fields`
- Fast selection using MongoDB aggregation (no Python full scans)
- Derived features for ML (cheap to compute, no extra API calls):
    * derived.channelAgeDays
    * derived.avgViewsPerVideo
    * derived.uploadFreqPerWeek (coarse estimate)

Collections
-----------
- videos(_hot): contains snippet.channelId, snippet.publishedAt
- channels: per-channel metadata & stats

Env
----
- .env at project root (auto-loaded)
  * YT_API_KEY=...
  * MONGO_URI=mongodb://localhost:27017/ytscan

CLI
---
- --videos-since-hours N   (only consider channels from videos newer than N hours)
- --stale-hours N          (refresh a channel only if last_checked_at older than N hours)
- --limit N                (hard cap for channels processed this run)
- --scan-all-channels      (ignore `videos` and refresh channels collection directly)
- --dry-run                (do not write to DB)
- --verbose                (extra logs)

Indexes (recommended)
---------------------
  db.channels.createIndex({ last_checked_at: 1 }, { name: "last_checked_at" })
  db.videos.createIndex({ "snippet.channelId": 1, "snippet.publishedAt": -1 }, { name: "chan_pub" })

Author: upgraded by ChatGPT
"""
from __future__ import annotations
import os, sys, math, time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

import requests
from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection

# ---------- Config & helpers ----------
EXIT_OK = 0
EXIT_ERR = 1
EXIT_NO_KEY = 2
EXIT_QUOTA = 88

# Load .env if present
from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv("YT_API_KEY")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


# ---------- API wrapper (quota-aware) ----------
class YTClient:
    def __init__(self, key: str, session: requests.Session | None = None) -> None:
        self.key = key
        self.sess = session or requests.Session()

    def get(self, url: str, params: Dict[str, Any], retries: int = 3, backoff: float = 2.0) -> Dict[str, Any]:
        params = dict(params)
        params.setdefault("key", self.key)
        for i in range(retries):
            r = self.sess.get(url, params=params, timeout=30)
            if r.status_code == 403:
                try:
                    err = r.json().get("error", {})
                    reasons = [e.get("reason") for e in err.get("errors", []) if isinstance(e, dict)]
                    if any(x in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded") for x in reasons):
                        print("[quota] YouTube quota exceeded → exit", file=sys.stderr)
                        sys.exit(EXIT_QUOTA)
                except Exception:
                    pass
            if r.ok:
                return r.json()
            # Retry on 5xx
            if r.status_code >= 500 and i < retries - 1:
                time.sleep(backoff * (i + 1))
                continue
            # Raise others
            try:
                r.raise_for_status()
            except Exception as e:
                print(f"[api] error: {e}", file=sys.stderr)
                raise
        raise RuntimeError("unreachable")


# ---------- Mongo channel pickers ----------

def pick_from_videos(db, videos_since_hours: int, stale_hours: int, limit: int, verbose: bool) -> List[str]:
    """Return distinct channelIds that are missing/stale in `channels`.
    Uses a single aggregation with $group + $lookup.
    """
    match_v: Dict[str, Any] = {"snippet.channelId": {"$exists": True, "$ne": None}}
    if videos_since_hours and videos_since_hours > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=videos_since_hours)).isoformat()
        match_v["snippet.publishedAt"] = {"$gte": cutoff}

    # staleness condition applied AFTER lookup
    if stale_hours and stale_hours > 0:
        cutoff2 = (datetime.now(timezone.utc) - timedelta(hours=stale_hours)).isoformat()
        stale_cond: Dict[str, Any] = {"$or": [
            {"channels": {"$exists": False}},
            {"channels": None},
            {"channels.last_checked_at": {"$exists": False}},
            {"channels.last_checked_at": {"$lt": cutoff2}},
            {"channels.stats": {"$exists": False}},
            {"channels.stats": None},
        ]}
    else:
        stale_cond = {"$or": [
            {"channels": {"$exists": False}},
            {"channels": None},
            {"channels.stats": {"$exists": False}},
            {"channels.stats": None},
        ]}

    pipeline = [
        {"$match": match_v},
        {"$group": {"_id": "$snippet.channelId"}},
        {"$match": {"_id": {"$regex": "^UC"}} },
        {"$lookup": {
            "from": "channels",
            "localField": "_id",
            "foreignField": "_id",
            "as": "channels"
        }},
        {"$unwind": {"path": "$channels", "preserveNullAndEmptyArrays": True}},
        {"$match": stale_cond},
        {"$project": {"_id": 1}}
    ]
    if limit and limit>0:
        pipeline.append({"$limit": int(limit)})
    ids = [d["_id"] for d in db.videos.aggregate(pipeline, allowDiskUse=True)]
    if verbose:
        print(f"[pick] from videos: {len(ids)} channels (sample={ids[:5]})")
    return ids


def pick_from_channels(db, stale_hours: int, limit: int, verbose: bool) -> List[str]:
    match_c: Dict[str, Any] = {}
    if stale_hours and stale_hours > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=stale_hours)).isoformat()
        match_c = {"$or": [
            {"last_checked_at": {"$exists": False}},
            {"last_checked_at": {"$lt": cutoff}},
            {"stats": {"$exists": False}},
            {"stats": None},
        ]}
    pipeline = [
        {"$match": match_c},
        {"$match": {"_id": {"$regex": "^UC"}} },
        {"$project": {"_id": 1}},
        {"$limit": int(limit or 2000)}
    ]
    if limit and limit>0:
        pipeline.append({"$limit": int(limit)})
    ids = [d["_id"] for d in db.channels.aggregate(pipeline, allowDiskUse=True)]
    if verbose:
        print(f"[pick] from channels: {len(ids)} channels (sample={ids[:5]})")
    return ids


# ---------- YouTube fetch ----------

def fetch_channels(yt: YTClient, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch snippet+statistics for up to 50 ids per request. Returns map id->object."""
    out: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return out
    for i in range(0, len(ids), 50):
        batch = ids[i:i+50]
        data = yt.get(CHANNELS_URL, {
            "part": "snippet,statistics",
            "id": ",".join(batch),
            "fields": "items(id,etag,snippet/publishedAt,snippet/title,snippet/customUrl,snippet/country,statistics/subscriberCount,statistics/videoCount,statistics/viewCount)"
        })
        items = data.get("items", []) or []
        if not items:
            print(f"[api] batch returned 0 items. sample ids={batch[:3]}")
        for it in items:
            cid = it.get("id")
            if not cid:
                continue
            out[cid] = it
    return out


# ---------- Derivations ----------

def to_int_or_none(v: Any) -> int | None:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.strip() == "":
            return None
        return int(v)
    except Exception:
        return None


def derive_features(snippet: Dict[str, Any] | None, stats: Dict[str, Any] | None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    ch_created = parse_iso_dt((snippet or {}).get("publishedAt"))
    age_days = None
    if ch_created:
        age_days = max(1, (now - ch_created).days)  # avoid zero

    subs = to_int_or_none((stats or {}).get("subscriberCount")) or 0
    vcount = to_int_or_none((stats or {}).get("videoCount")) or 0
    vviews = to_int_or_none((stats or {}).get("viewCount")) or 0

    avg_views = float(vviews) / float(max(vcount, 1)) if vviews is not None else None
    uploads_per_week = None
    if age_days is not None and age_days > 0:
        uploads_per_week = float(vcount) / float(age_days / 7.0)

    return {
        "channelAgeDays": age_days,
        "avgViewsPerVideo": avg_views,
        "uploadFreqPerWeek": uploads_per_week,
    }


def extract_handle(custom_url: str | None) -> str | None:
    if not custom_url:
        return None
    h = custom_url.strip()
    if not h:
        return None
    if h.startswith("@"):
        return h
    return "@" + h


# ---------- Upsert ----------

def upsert_channels(col: Collection, docs: Dict[str, Dict[str, Any]], dry_run: bool, verbose: bool) -> int:
    if not docs:
        return 0
    ops: List[UpdateOne] = []
    now = now_iso()
    for cid, raw in docs.items():
        snippet = raw.get("snippet") or {}
        statistics = raw.get("statistics") or {}
        etag = raw.get("etag")

        doc_set = {
            "_id": cid,
            "snippet": {
                "title": snippet.get("title"),
                "handle": extract_handle(snippet.get("customUrl")),
                "publishedAt": snippet.get("publishedAt"),
                "country": snippet.get("country")
            },
            "stats": {
                "subscriberCount": to_int_or_none(statistics.get("subscriberCount")),
                "videoCount": to_int_or_none(statistics.get("videoCount")),
                "viewCount": to_int_or_none(statistics.get("viewCount"))
            },
            "derived": derive_features(snippet, statistics),
            "last_checked_at": now,
        }
        if etag:
            doc_set["etag"] = etag
        ops.append(UpdateOne({"_id": cid}, {"$set": doc_set}, upsert=True))

    if dry_run:
        sample = list(docs.keys())[:5]
        print(f"[dry-run] would upsert {len(docs)} channels; sample={sample}")
        return 0

    if ops:
        res = col.bulk_write(ops, ordered=False)
        upserts = (res.upserted_count or 0) + (res.modified_count or 0)
        if verbose:
            print(f"[upsert] matched={res.matched_count} modified={res.modified_count} upserted={res.upserted_count}")
        return upserts
    return 0


# ---------- Main ----------

def parse_args(argv: List[str]):
    import argparse
    p = argparse.ArgumentParser(description="Backfill channels (v2)")
    p.add_argument("--videos-since-hours", type=int, default=72,
                   help="Only consider channels from videos newer than N hours (default: 72). Set 0 to ignore.")
    p.add_argument("--stale-hours", type=int, default=48,
                   help="Only refresh channels whose last_checked_at is older than N hours (default: 48). Set 0 to ignore.")
    p.add_argument("--limit", type=int, default=2000, help="Maximum channels to process in this run (default: 2000). Use 0 for unlimited.")
    p.add_argument("--scan-all-channels", action="store_true",
                   help="Refresh from the entire 'channels' collection instead of discovering via 'videos'.")
    p.add_argument("--dry-run", action="store_true", help="Do not write to DB; print summary only")
    p.add_argument("--verbose", action="store_true", help="Verbose logs")
    p.add_argument("--loop-until-empty", action="store_true", help="Keep looping until no more channels picked")
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    if not API_KEY:
        print("Missing YT_API_KEY env.", file=sys.stderr)
        return EXIT_NO_KEY

    args = parse_args(argv)

    client = MongoClient(MONGO_URI)
    db = client.get_database()

    print(
        f">>> backfill_channels | scan_all={args.scan_all_channels} | videos_since_hours={args.videos_since_hours} "
        f"| stale_hours={args.stale_hours} | limit={args.limit} | dry_run={args.dry_run}"
    )

    # loop-until-empty mode
    if args.loop_until_empty:
        total_changed = 0
        loop_count = 0
        while True:
            loop_count += 1
            print(f"[loop] Run #{loop_count} ...")

            if args.scan_all_channels:
                chan_ids = pick_from_channels(db, args.stale_hours, args.limit, args.verbose)
            else:
                chan_ids = pick_from_videos(db, args.videos_since_hours, args.stale_hours, args.limit, args.verbose)

            if not chan_ids:
                print(f"[loop] nothing to do — exit after {loop_count-1} runs")
                print(f"SUMMARY: total_changed={total_changed}")
                return EXIT_OK

            yt = YTClient(API_KEY)
            fetched = fetch_channels(yt, chan_ids)
            changed = upsert_channels(db.channels, fetched, args.dry_run, args.verbose)
            total_changed += changed
            print(f"[loop] picked={len(chan_ids)} fetched={len(fetched)} changed={changed}")

            if changed == 0:
                print(f"[loop] No more changes detected — exit after {loop_count} runs")
                print(f"SUMMARY: total_changed={total_changed}")
                return EXIT_OK

    # pick channel ids
    if args.scan_all_channels:
        chan_ids = pick_from_channels(db, args.stale_hours, args.limit, args.verbose)
    else:
        chan_ids = pick_from_videos(db, args.videos_since_hours, args.stale_hours, args.limit, args.verbose)

    if not chan_ids:
        print("[pick] nothing to do.")
        return EXIT_OK

    # fetch & upsert
    yt = YTClient(API_KEY)
    fetched = fetch_channels(yt, chan_ids)
    changed = upsert_channels(db.channels, fetched, args.dry_run, args.verbose)

    print(f"DONE. picked={len(chan_ids)} fetched={len(fetched)} changed={changed}")
    if len(chan_ids) > 0 and len(fetched) == 0:
        print("[hint] Picked > 0 but fetched = 0 → check YT_API_KEY/.env or invalid channelIds. Use --verbose to see sample ids.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
