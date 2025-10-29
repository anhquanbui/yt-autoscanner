#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backfill channel metadata & analytics (v3, laptop-ready)

What’s new
----------
- Channel-level rolling analytics từ DB (không tốn thêm quota):
    * recentUploadCount_30d, avg_views_30d, avg_growth_rate_30d, viral_ratio_30d
    * median_views_1440 (dùng $percentile hoặc fallback Python), std_views_1440
    * avg_like_rate, avg_comment_rate
- Derived (cho ML):
    * view_efficiency_mean / powerlaw, channel_stability_index, channel_activity_score,
      channel_trust_score (0.5–2.0 để nhân vào sample_weight)
- CLI tiện dụng:
    --window-days, --processed-collection, --videos-collection,
    --scan-all-channels, --only-analytics, --no-api, --loop-until-empty, --limit
- An toàn quota: có thể chạy --only-analytics (không gọi API)
- Fix Mongo: dùng $percentile(p=0.5) ≈ median, fallback sang Python nếu server không hỗ trợ

Indexes gợi ý
-------------
  db.channels.createIndex({ last_checked_at: 1 }, { name: "last_checked_at" })
  db.channels.createIndex({ "snippet.country": 1 }, { name: "country" })
  db.channels.createIndex({ "analytics.viral_ratio_30d": -1 }, { name: "viral_ratio_30d_desc" })
  db.videos.createIndex({ "snippet.channelId": 1, "snippet.publishedAt": -1 }, { name: "chan_pub" })
  db.processed.createIndex({ "source_meta.channelId": 1, "source_meta.publish_ts": -1 }, { name: "proc_chan_ts" })

Env cần thiết
-------------
- MONGO_URI (mặc định: mongodb://localhost:27017/ytscan)
- YT_API_KEY (không bắt buộc nếu dùng --only-analytics hoặc --no-api)
"""

from __future__ import annotations
import os, sys, math, time, json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

import requests
import numpy as np
from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import OperationFailure

# ---------- Exit codes ----------
EXIT_OK = 0
EXIT_ERR = 1
EXIT_NO_KEY = 2
EXIT_QUOTA = 88

# ---------- Env ----------
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

API_KEY = os.getenv("YT_API_KEY")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

# stdout encoding (Windows-safe best-effort)
for _s in (sys.stdout, sys.stderr):
    try:
        _.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_dt().isoformat()


def parse_iso_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def to_int_or_none(v: Any) -> int | None:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            v = v.strip()
            if v == "":
                return None
            return int(float(v))
        return int(v)
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


# ---------- Pickers ----------
def pick_from_videos(db, videos_since_hours: int, stale_hours: int, limit: int, verbose: bool) -> List[str]:
    """Pick distinct channelIds referenced by recent videos and stale/missing in channels."""
    match_v: Dict[str, Any] = {"snippet.channelId": {"$exists": True, "$ne": None}}
    if videos_since_hours and videos_since_hours > 0:
        cutoff = (now_dt() - timedelta(hours=videos_since_hours)).isoformat()
        match_v["snippet.publishedAt"] = {"$gte": cutoff}

    if stale_hours and stale_hours > 0:
        cutoff2 = (now_dt() - timedelta(hours=stale_hours)).isoformat()
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
        {"$match": {"_id": {"$regex": "^UC"}}},
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
    if limit and limit > 0:
        pipeline.append({"$limit": int(limit)})

    ids = [d["_id"] for d in db.videos.aggregate(pipeline, allowDiskUse=True)]
    if verbose:
        print(f"[pick] from videos: {len(ids)} channels (sample={ids[:5]})")
    return ids


def pick_from_channels(db, stale_hours: int, limit: int, verbose: bool) -> List[str]:
    match_c: Dict[str, Any] = {}
    if stale_hours and stale_hours > 0:
        cutoff = (now_dt() - timedelta(hours=stale_hours)).isoformat()
        match_c = {"$or": [
            {"last_checked_at": {"$exists": False}},
            {"last_checked_at": {"$lt": cutoff}},
            {"stats": {"$exists": False}},
            {"stats": None},
        ]}
    pipeline = [
        {"$match": match_c},
        {"$match": {"_id": {"$regex": "^UC"}}},
        {"$project": {"_id": 1}},
    ]
    if limit and limit > 0:
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
            "fields": (
                "items(id,etag,"
                "snippet/publishedAt,snippet/title,snippet/customUrl,snippet/country,"
                "statistics/subscriberCount,statistics/videoCount,statistics/viewCount)"
            )
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


# ---------- Analytics from DB ----------
def compute_channel_analytics(db,
                              channel_ids: List[str],
                              processed_col: str = "processed",
                              videos_col: str = "videos",
                              window_days: int = 30) -> Dict[str, Dict[str, Any]]:
    """
    Build rolling channel analytics from `processed` (preferred) or fallback to `videos`.
    Uses $percentile (p=0.5) for median when available; falls back to Python median otherwise.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not channel_ids:
        return out

    cutoff = (now_dt() - timedelta(days=window_days)).isoformat()
    proc = db.get_collection(processed_col)
    vids = db.get_collection(videos_col)

    # Prefer `processed` because it has horizons + labels
    pipe_proc_percentile = [
        {"$match": {
            "source_meta.channelId": {"$in": channel_ids},
            # Fallback nếu publish_ts chưa được đổ vào processed
            "$or": [
                {"source_meta.publish_ts": {"$gte": cutoff}},
                {"snippet.publishedAt": {"$gte": cutoff}}
            ]
        }},
        {"$project": {
            "_id": 0,
            "channelId": "$source_meta.channelId",
            "views_60": "$horizons.60.views",
            "views_1440": "$horizons.1440.views",
            "likes_1440": "$horizons.1440.likes",
            "comments_1440": "$horizons.1440.comments",
            "like_rate_1440": {"$cond": [
                {"$gt": ["$horizons.1440.views", 0]},
                {"$divide": ["$horizons.1440.likes", "$horizons.1440.views"]},
                None
            ]},
            "comment_rate_1440": {"$cond": [
                {"$gt": ["$horizons.1440.views", 0]},
                {"$divide": ["$horizons.1440.comments", "$horizons.1440.views"]},
                None
            ]},
            "growth_rate": {"$cond": [
                {"$gt": ["$horizons.60.views", 0]},
                {"$divide": [{"$add": ["$horizons.1440.views", 1]}, {"$add": ["$horizons.60.views", 1]}]},
                None
            ]},
            "is_viral": "$ml_flags.likely_viral"
        }},
        {"$group": {
            "_id": "$channelId",
            "recentUploadCount_30d": {"$sum": 1},
            "avg_views_30d": {"$avg": "$views_1440"},
            "avg_growth_rate_30d": {"$avg": "$growth_rate"},
            "viral_ratio_30d": {"$avg": "$is_viral"},
            # median ≈ p50 bằng $percentile (approximate); nếu server không hỗ trợ sẽ vào except
            "median_vec": {"$percentile": {"p": [0.5], "input": "$views_1440", "method": "approximate"}},
            "std_views_1440": {"$stdDevSamp": "$views_1440"},
            "avg_like_rate": {"$avg": "$like_rate_1440"},
            "avg_comment_rate": {"$avg": "$comment_rate_1440"},
        }},
        {"$project": {
            "_id": 0,
            "channelId": "$_id",
            "recentUploadCount_30d": 1,
            "avg_views_30d": 1,
            "avg_growth_rate_30d": 1,
            "viral_ratio_30d": 1,
            "median_views_1440": {"$arrayElemAt": ["$median_vec", 0]},
            "std_views_1440": 1,
            "avg_like_rate": 1,
            "avg_comment_rate": 1
        }}
    ]

    try:
        proc_docs = list(proc.aggregate(pipe_proc_percentile, allowDiskUse=True))
    except OperationFailure:
        # Fallback: không có $percentile → gom mảng và tính median bên Python
        pipe_proc_push = [
            {"$match": {
                "source_meta.channelId": {"$in": channel_ids},
                "$or": [
                    {"source_meta.publish_ts": {"$gte": cutoff}},
                    {"snippet.publishedAt": {"$gte": cutoff}}
                ]
            }},
            {"$project": {
                "_id": 0,
                "channelId": "$source_meta.channelId",
                "views_60": "$horizons.60.views",
                "views_1440": "$horizons.1440.views",
                "likes_1440": "$horizons.1440.likes",
                "comments_1440": "$horizons.1440.comments",
                "like_rate_1440": {"$cond": [
                    {"$gt": ["$horizons.1440.views", 0]},
                    {"$divide": ["$horizons.1440.likes", "$horizons.1440.views"]},
                    None
                ]},
                "comment_rate_1440": {"$cond": [
                    {"$gt": ["$horizons.1440.views", 0]},
                    {"$divide": ["$horizons.1440.comments", "$horizons.1440.views"]},
                    None
                ]},
                "growth_rate": {"$cond": [
                    {"$gt": ["$horizons.60.views", 0]},
                    {"$divide": [{"$add": ["$horizons.1440.views", 1]}, {"$add": ["$horizons.60.views", 1]}]},
                    None
                ]},
                "is_viral": "$ml_flags.likely_viral"
            }},
            {"$group": {
                "_id": "$channelId",
                "recentUploadCount_30d": {"$sum": 1},
                "avg_views_30d": {"$avg": "$views_1440"},
                "avg_growth_rate_30d": {"$avg": "$growth_rate"},
                "viral_ratio_30d": {"$avg": "$is_viral"},
                "views_arr": {"$push": "$views_1440"},
                "std_views_1440": {"$stdDevSamp": "$views_1440"},
                "avg_like_rate": {"$avg": "$like_rate_1440"},
                "avg_comment_rate": {"$avg": "$comment_rate_1440"},
            }},
            {"$project": {
                "_id": 0,
                "channelId": "$_id",
                "recentUploadCount_30d": 1,
                "avg_views_30d": 1,
                "avg_growth_rate_30d": 1,
                "viral_ratio_30d": 1,
                "views_arr": 1,
                "std_views_1440": 1,
                "avg_like_rate": 1,
                "avg_comment_rate": 1
            }}
        ]
        tmp_docs = list(proc.aggregate(pipe_proc_push, allowDiskUse=True))
        proc_docs = []
        for d in tmp_docs:
            arr = [v for v in (d.get("views_arr") or []) if v is not None]
            med = float(np.median(arr)) if arr else None
            d.pop("views_arr", None)
            d["median_views_1440"] = med
            proc_docs.append(d)

    for d in proc_docs:
        out[d["channelId"]] = d

    # Fallback sang `videos` nếu kênh không có processed trong window
    missing = [cid for cid in channel_ids if cid not in out]
    if missing:
        pipe_vid = [
            {"$match": {
                "snippet.channelId": {"$in": missing},
                "snippet.publishedAt": {"$gte": cutoff}
            }},
            {"$group": {
                "_id": "$snippet.channelId",
                "recentUploadCount_30d": {"$sum": 1}
            }},
            {"$project": {"_id": 0, "channelId": "$_id", "recentUploadCount_30d": 1}}
        ]
        for d in vids.aggregate(pipe_vid, allowDiskUse=True):
            out.setdefault(d["channelId"], {}).update(d)

    return out


def post_derive_with_subscribers(analytics: Dict[str, Any], subscribers: int | None) -> Dict[str, Any]:
    """Compute efficiency / trust / activity using subscriber count."""
    subs = max(0, int(subscribers or 0))
    eps = 1.0
    v_mean = analytics.get("avg_views_30d") or analytics.get("median_views_1440") or 0
    std_v = analytics.get("std_views_1440") or 0
    freq = analytics.get("recentUploadCount_30d") or 0
    growth = analytics.get("avg_growth_rate_30d") or 0
    viral = analytics.get("viral_ratio_30d") or 0

    # Efficiency variants
    eff_mean  = (v_mean + eps) / (subs + eps)
    eff_power = (v_mean + eps) / math.pow(subs + eps, 0.8)

    # Stability index: std / mean (guard 0)
    stability = float(std_v) / float(v_mean + eps)

    # Activity score: simple blend of frequency & views (normalize gently)
    activity = 0.5 * (freq / 8.0) + 0.5 * (math.log1p(v_mean) / 15.0)
    activity = max(0.0, min(activity, 2.0))

    # Trust score: weighted composite then clipped to [0.5, 2.0]
    base = math.log1p(subs) / 10.0
    trust = 0.35 * eff_power + 0.25 * viral + 0.25 * activity + 0.15 * base
    trust = float(max(0.5, min(trust, 2.0)))

    return {
        "view_efficiency_mean": float(eff_mean),
        "view_efficiency_powerlaw": float(eff_power),
        "channel_stability_index": float(stability),
        "channel_activity_score": float(activity),
        "channel_trust_score": float(trust),
    }


# ---------- Derivations from API snippet/stats ----------
def derive_static(snippet: Dict[str, Any] | None, stats: Dict[str, Any] | None) -> Dict[str, Any]:
    now = now_dt()
    ch_created = parse_iso_dt((snippet or {}).get("publishedAt"))
    age_days = None
    if ch_created:
        age_days = max(1, (now - ch_created).days)

    vcount = to_int_or_none((stats or {}).get("videoCount")) or 0
    uploads_per_week = None
    if age_days and age_days > 0:
        uploads_per_week = float(vcount) / float(age_days / 7.0)

    vviews = to_int_or_none((stats or {}).get("viewCount")) or 0
    avg_views = float(vviews) / float(max(vcount, 1))

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
def upsert_channels(col: Collection,
                    raw_api: Dict[str, Dict[str, Any]],
                    analytics_map: Dict[str, Dict[str, Any]],
                    dry_run: bool,
                    verbose: bool) -> int:
    if not raw_api and not analytics_map:
        return 0

    ops: List[UpdateOne] = []
    now = now_iso()

    # Upsert union ids có ở API hoặc analytics
    all_ids = set(raw_api.keys()) | set(analytics_map.keys())
    for cid in all_ids:
        api_obj = raw_api.get(cid) or {}
        snippet = api_obj.get("snippet") or {}
        statistics = api_obj.get("statistics") or {}
        etag = api_obj.get("etag")

        doc_set: Dict[str, Any] = {}
        # Static/API block (optional khi --no-api / --only-analytics)
        if api_obj:
            doc_set.update({
                "_id": cid,
                "snippet": {
                    "title": snippet.get("title"),
                    "handle": extract_handle(snippet.get("customUrl")),
                    "publishedAt": snippet.get("publishedAt"),
                    "country": snippet.get("country"),
                },
                "stats": {
                    "subscriberCount": to_int_or_none(statistics.get("subscriberCount")),
                    "videoCount": to_int_or_none(statistics.get("videoCount")),
                    "viewCount": to_int_or_none(statistics.get("viewCount")),
                },
            })
            if etag:
                doc_set["etag"] = etag

            # Derived từ API
            doc_set.setdefault("derived", {})
            doc_set["derived"].update(derive_static(snippet, statistics))

        # Analytics (from DB) — luôn có thể set
        if analytics_map.get(cid):
            doc_set.setdefault("analytics", {})
            doc_set["analytics"].update(analytics_map[cid])

        # Post-derivation (cần subscriberCount)
        subs_for_eff = None
        try:
            subs_for_eff = int(doc_set.get("stats", {}).get("subscriberCount") or 0)
        except Exception:
            subs_for_eff = None

        if doc_set.get("analytics") is not None:
            ml_add = post_derive_with_subscribers(doc_set["analytics"], subs_for_eff)
            doc_set["analytics"].update(ml_add)

        # Bookkeeping
        doc_set["last_checked_at"] = now

        if not doc_set:
            continue

        if verbose:
            preview = json.dumps(
                {k: v for k, v in doc_set.items() if k in ("stats", "analytics")},
                ensure_ascii=False
            )[:160]
            print(f"[upsert.prepare] {cid} :: {preview}")

        ops.append(UpdateOne({"_id": cid}, {"$set": doc_set}, upsert=True))

    if dry_run:
        print(f"[dry-run] would upsert {len(ops)} channels; sample={list(all_ids)[:5]}")
        return 0

    if ops:
        res = col.bulk_write(ops, ordered=False)
        if verbose:
            print(f"[upsert] matched={res.matched_count} modified={res.modified_count} upserted={res.upserted_count}")
        return (res.upserted_count or 0) + (res.modified_count or 0)
    return 0


# ---------- CLI ----------
def parse_args(argv: List[str]):
    import argparse
    p = argparse.ArgumentParser(description="Backfill channels (v3)")
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

    # New options
    p.add_argument("--window-days", type=int, default=30, help="Rolling window in days for analytics (default: 30)")
    p.add_argument("--processed-collection", type=str, default="processed", help="Collection name for processed docs (default: processed)")
    p.add_argument("--videos-collection", type=str, default="videos", help="Collection name for videos (default: videos)")
    p.add_argument("--only-analytics", action="store_true", help="Compute & upsert analytics from DB only (skip API fetch)")
    p.add_argument("--no-api", action="store_true", help="Alias to skip YouTube API fetch in this run")
    return p.parse_args(argv)


# ---------- Main ----------
def main(argv: List[str]) -> int:
    args = parse_args(argv)

    if not API_KEY and not (args.only_analytics or args.no_api):
        print("Missing YT_API_KEY env. Use --only-analytics or --no-api to skip API.", file=sys.stderr)
        return EXIT_NO_KEY

    client = MongoClient(MONGO_URI)
    db = client.get_database()

    print(
        f">>> backfill_channels v3 | scan_all={args.scan_all_channels} | videos_since_hours={args.videos_since_hours} "
        f"| stale_hours={args.stale_hours} | limit={args.limit} | window_days={args.window_days} "
        f"| only_analytics={args.only_analytics} | no_api={args.no_api} | dry_run={args.dry_run}"
    )

    # Pick channel ids
    if args.scan_all_channels:
        chan_ids = pick_from_channels(db, args.stale_hours, args.limit, args.verbose)
    else:
        chan_ids = pick_from_videos(db, args.videos_since_hours, args.stale_hours, args.limit, args.verbose)

    if not chan_ids:
        print("[pick] nothing to do.")
        return EXIT_OK

    # Compute analytics from DB (processed preferred; videos fallback)
    analytics_map = compute_channel_analytics(
        db,
        chan_ids,
        processed_col=args.processed_collection,
        videos_col=args.videos_collection,
        window_days=args.window_days,
    )

    # Optionally fetch API for static/stats
    raw_api: Dict[str, Dict[str, Any]] = {}
    if not (args.only_analytics or args.no_api):
        yt = YTClient(API_KEY)
        raw_api = fetch_channels(yt, chan_ids)

    changed = upsert_channels(db.channels, raw_api, analytics_map, args.dry_run, args.verbose)
    print(f"DONE. picked={len(chan_ids)} fetched={len(raw_api)} changed={changed}")

    if len(chan_ids) > 0 and not raw_api and not (args.only_analytics or args.no_api):
        print("[hint] Picked > 0 but fetched = 0 → check YT_API_KEY/.env hoặc channelId không hợp lệ. Dùng --verbose để xem sample.")

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
