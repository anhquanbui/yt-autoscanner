#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backfill channel metadata & analytics (v3, laptop-ready)

What this script does
---------------------
- Refresh or create documents in the `channels` collection using:
    * Live data from the YouTube Data API (snippet, statistics).
    * Rolling channel analytics computed from MongoDB data (`processed` / `videos`).

- Channel-level rolling analytics (from DB; no extra YouTube quota):
    * recentUploadCount_30d, avg_views_30d, avg_growth_rate_30d, viral_ratio_30d
    * median_views_1440 (via $percentile where available, or Python median fallback)
    * std_views_1440, avg_like_rate, avg_comment_rate

- Derived features for ML / sampling:
    * view_efficiency_mean (views relative to subscribers, power-law style)
    * channel_stability_index, channel_activity_score
    * channel_trust_score (typ. 0.5–2.0, can be used to scale sample_weight)

- Flexible CLI:
    --window-days, --processed-collection, --videos-collection,
    --scan-all-channels, --only-analytics, --no-api, --loop-until-empty, --limit

- Quota-safe:
    You can run in analytics-only mode (no API calls) with --only-analytics or --no-api.


Suggested indexes
-----------------
    db.channels.createIndex({ last_checked_at: 1 }, { name: "last_checked_at" })
    db.channels.createIndex({ "snippet.country": 1 }, { name: "country" })
    db.channels.createIndex({ "analytics.viral_ratio_30d": -1 }, { name: "viral_ratio_30d_desc" })
    db.videos.createIndex({ "snippet.channelId": 1, "snippet.publishedAt": -1 }, { name: "chan_pub" })
    db.processed.createIndex({ "source_meta.channelId": 1, "source_meta.publish_ts": -1 }, { name: "proc_chan_ts" })

Required environment
--------------------
- MONGO_URI   (default: mongodb://localhost:27017/ytscan)
- YT_API_KEY  (not required if you only run with --only-analytics / --no-api)
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests
from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection

# ---------- Exit codes ----------
EXIT_OK = 0
EXIT_ERR = 1
EXIT_NO_KEY = 2
EXIT_QUOTA = 88

# ---------- Env (unified via config.env) ----------
from config.env import load_env, get_env

# Load .env once using the shared priority search:
#   1) ~/.env
#   2) <project_root>/.env
#   3) Any immediate or nested subfolder .env under the project.
load_env()

API_KEY = get_env("YT_API_KEY")
MONGO_URI = get_env("MONGO_URI", "mongodb://localhost:27017/ytscan")

# ---------- Console UTF-8 safety ----------
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    # Older Python / weird shells
    import io as _io

    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ---------- Helpers ----------
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
        return int(v)
    except Exception:
        return None


# ---------- YouTube client ----------
@dataclass
class YTClient:
    key: str
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()

    def get(self, url: str, params: Dict[str, Any], retries: int = 3, backoff: float = 2.0) -> Dict[str, Any]:
        params = dict(params)
        params.setdefault("key", self.key)
        for i in range(retries):
            r = self.session.get(url, params=params, timeout=30)
            if r.status_code == 403:
                # Try to inspect quota errors
                try:
                    err = r.json().get("error", {})
                    reasons = [e.get("reason") for e in err.get("errors", []) if isinstance(e, dict)]
                    if any(
                        x in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded")
                        for x in reasons
                    ):
                        print("[ERROR] YouTube quota exhausted.", file=sys.stderr)
                        raise SystemExit(EXIT_QUOTA)
                except Exception:
                    pass
            try:
                r.raise_for_status()
                return r.json()
            except requests.HTTPError as e:
                if i == retries - 1:
                    print(f"[ERROR] YTClient GET failed after {retries} attempts: {e}", file=sys.stderr)
                    raise
                sleep_sec = backoff * (2**i)
                print(f"[WARN] YTClient GET failed (attempt {i+1}/{retries}), retry in {sleep_sec:.1f}s...", file=sys.stderr)
                import time as _time

                _time.sleep(sleep_sec)
        # Should not reach here
        raise RuntimeError("YTClient.get unexpected fall-through")


# ---------- Pick candidate channelIds ----------
def pick_from_videos(db, videos_since_hours: int, stale_hours: int, limit: int, verbose: bool) -> List[str]:
    """Pick distinct channelIds referenced by recent videos and that are stale/missing in `channels`."""
    match_v: Dict[str, Any] = {"snippet.channelId": {"$exists": True, "$ne": None}}
    if videos_since_hours and videos_since_hours > 0:
        cutoff = (now_dt() - timedelta(hours=videos_since_hours)).isoformat()
        match_v["snippet.publishedAt"] = {"$gte": cutoff}

    stale_cond: Dict[str, Any] = {}
    if stale_hours and stale_hours > 0:
        cutoff2 = (now_dt() - timedelta(hours=stale_hours)).isoformat()
        stale_cond = {
            "$or": [
                {"channels": {"$exists": False}},
                {"channels": None},
                {"channels.last_checked_at": {"$exists": False}},
                {"channels.last_checked_at": {"$lt": cutoff2}},
                {"channels.stats": {"$exists": False}},
                {"channels.stats": None},
            ]
        }

    pipeline: List[Dict[str, Any]] = [
        {"$match": match_v},
        {"$group": {"_id": "$snippet.channelId"}},
        {
            "$lookup": {
                "from": "channels",
                "localField": "_id",
                "foreignField": "_id",
                "as": "channels",
            }
        },
        {"$unwind": {"path": "$channels", "preserveNullAndEmptyArrays": True}},
    ]
    if stale_cond:
        pipeline.append({"$match": stale_cond})
    pipeline.append({"$project": {"_id": 1}})

    if limit and limit > 0:
        pipeline.append({"$limit": int(limit)})

    ids = [d["_id"] for d in db.videos.aggregate(pipeline, allowDiskUse=True)]
    # Ensure unique channel ids while preserving order
    ids = list(dict.fromkeys(ids))
    if verbose:
        print(f"[pick] from videos: {len(ids)} channels (sample={ids[:5]})")
    return ids


def pick_from_channels(db, stale_hours: int, limit: int, verbose: bool) -> List[str]:
    """Pick channelIds directly from `channels` collection (used when --scan-all-channels)."""
    match_c: Dict[str, Any] = {}
    if stale_hours and stale_hours > 0:
        cutoff = (now_dt() - timedelta(hours=stale_hours)).isoformat()
        match_c = {
            "$or": [
                {"last_checked_at": {"$exists": False}},
                {"last_checked_at": {"$lt": cutoff}},
                {"stats": {"$exists": False}},
                {"stats": None},
            ]
        }

    pipeline: List[Dict[str, Any]] = [
        {"$match": match_c},
        {"$match": {"_id": {"$regex": "^UC"}}},
        {"$project": {"_id": 1}},
    ]
    if limit and limit > 0:
        pipeline.append({"$limit": int(limit)})

    ids = [d["_id"] for d in db.channels.aggregate(pipeline, allowDiskUse=True)]
    # Ensure unique channel ids while preserving order
    ids = list(dict.fromkeys(ids))
    if verbose:
        print(f"[pick] from channels: {len(ids)} channels (sample={ids[:5]})")
    return ids


# ---------- YouTube fetch ----------
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


def fetch_channels(yt: YTClient, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch channels via YouTube Data API and return a dict: {channelId: raw_item}."""
    out: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return out

    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        params = {
            "part": "snippet,statistics",
            "id": ",".join(batch),
            "maxResults": len(batch),
        }
        data = yt.get(CHANNELS_URL, params)
        for item in data.get("items", []):
            cid = item.get("id")
            if cid:
                out[cid] = item
    return out


# ---------- Analytics from Mongo ----------
def compute_channel_analytics(
    db,
    channel_ids: List[str],
    processed_col: str = "processed",
    videos_col: str = "videos",
    window_days: int = 30,
) -> Dict[str, Dict[str, Any]]:
    """
    Build rolling channel analytics from `processed` (preferred) or fall back to `videos`.

    Uses $percentile (p=0.5) where available to approximate median, and falls back to
    computing the median in Python if the server does not support it.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not channel_ids:
        return out

    cutoff = (now_dt() - timedelta(days=window_days)).isoformat()
    proc = db.get_collection(processed_col)
    vids = db.get_collection(videos_col)

    # 1) Analytics from `processed`
    pipeline = [
        {
            "$match": {
                "source_meta.channelId": {"$in": channel_ids},
                "source_meta.publish_ts": {"$gte": cutoff},
            }
        },
        {
            "$project": {
                "channelId": "$source_meta.channelId",
                "publish_ts": "$source_meta.publish_ts",
                "views_1440": "$horizons.1440.views",
                "views_60": "$horizons.60.views",
                "likes_1440": "$horizons.1440.likes",
                "comments_1440": "$horizons.1440.comments",
                "is_viral": "$ml_flags.likely_viral",
            }
        },
        {
            "$addFields": {
                # Fallback when publish_ts has not been populated into `processed` yet
                "publish_ts": {
                    "$ifNull": ["$publish_ts", "$_id"],
                },
                "growth_rate": {
                    "$cond": [
                        {"$gt": ["$views_60", 0]},
                        {"$divide": [{"$add": ["$views_1440", 1]}, {"$add": ["$views_60", 1]}]},
                        None,
                    ]
                },
                "like_rate": {
                    "$cond": [
                        {"$gt": ["$views_1440", 0]},
                        {"$divide": ["$likes_1440", "$views_1440"]},
                        None,
                    ]
                },
                "comment_rate": {
                    "$cond": [
                        {"$gt": ["$views_1440", 0]},
                        {"$divide": ["$comments_1440", "$views_1440"]},
                        None,
                    ]
                },
            }
        },
        {
            "$group": {
                "_id": "$channelId",
                "recentUploadCount_30d": {"$sum": 1},
                "avg_views_30d": {"$avg": "$views_1440"},
                "avg_growth_rate_30d": {"$avg": "$growth_rate"},
                "viral_ratio_30d": {"$avg": "$is_viral"},
                "avg_like_rate": {"$avg": "$like_rate"},
                "avg_comment_rate": {"$avg": "$comment_rate"},
                "views_array": {"$push": "$views_1440"},
            }
        },
    ]

    docs = list(proc.aggregate(pipeline, allowDiskUse=True))
    for d in docs:
        cid = d.get("_id")
        if not cid:
            continue
        views_array: List[int] = [int(x) for x in d.get("views_array", []) if isinstance(x, (int, float))]
        views_array.sort()
        median_val: Optional[float] = None
        if views_array:
            mid = len(views_array) // 2
            if len(views_array) % 2 == 1:
                median_val = float(views_array[mid])
            else:
                median_val = (views_array[mid - 1] + views_array[mid]) / 2.0

        std_val: Optional[float] = None
        if views_array and len(views_array) > 1:
            mean = sum(views_array) / len(views_array)
            var = sum((x - mean) ** 2 for x in views_array) / (len(views_array) - 1)
            std_val = math.sqrt(max(var, 0.0))

        out[cid] = {
            "recentUploadCount_30d": int(d.get("recentUploadCount_30d") or 0),
            "avg_views_30d": float(d.get("avg_views_30d") or 0.0),
            "avg_growth_rate_30d": float(d.get("avg_growth_rate_30d") or 0.0),
            "viral_ratio_30d": float(d.get("viral_ratio_30d") or 0.0),
            "avg_like_rate": float(d.get("avg_like_rate") or 0.0),
            "avg_comment_rate": float(d.get("avg_comment_rate") or 0.0),
            "median_views_1440": median_val,
            "std_views_1440": std_val,
        }

    # 2) Fallback to `videos` if a channel has no `processed` docs within the window
    missing = [cid for cid in channel_ids if cid not in out]
    if missing:
        pipeline_v = [
            {
                "$match": {
                    "snippet.channelId": {"$in": missing},
                    "snippet.publishedAt": {"$gte": cutoff},
                    "stats_snapshots.0": {"$exists": True},
                }
            },
            {
                "$addFields": {
                    "snap_last": {"$arrayElemAt": ["$stats_snapshots", -1]},
                }
            },
            {
                "$project": {
                    "channelId": "$snippet.channelId",
                    "views_1440": "$snap_last.viewCount",
                }
            },
            {
                "$group": {
                    "_id": "$channelId",
                    "recentUploadCount_30d": {"$sum": 1},
                    "avg_views_30d": {"$avg": "$views_1440"},
                    "views_array": {"$push": "$views_1440"},
                }
            },
        ]
        docs_v = list(vids.aggregate(pipeline_v, allowDiskUse=True))
        for d in docs_v:
            cid = d.get("_id")
            if not cid:
                continue
            if cid in out:
                continue
            views_array: List[int] = [int(x) for x in d.get("views_array", []) if isinstance(x, (int, float))]
            views_array.sort()
            median_val: Optional[float] = None
            if views_array:
                mid = len(views_array) // 2
                if len(views_array) % 2 == 1:
                    median_val = float(views_array[mid])
                else:
                    median_val = (views_array[mid - 1] + views_array[mid]) / 2.0

            std_val: Optional[float] = None
            if views_array and len(views_array) > 1:
                mean = sum(views_array) / len(views_array)
                var = sum((x - mean) ** 2 for x in views_array) / (len(views_array) - 1)
                std_val = math.sqrt(max(var, 0.0))

            out[cid] = {
                "recentUploadCount_30d": int(d.get("recentUploadCount_30d") or 0),
                "avg_views_30d": float(d.get("avg_views_30d") or 0.0),
                "avg_growth_rate_30d": None,
                "viral_ratio_30d": None,
                "avg_like_rate": None,
                "avg_comment_rate": None,
                "median_views_1440": median_val,
                "std_views_1440": std_val,
            }

    return out


# ---------- Post-derivation from subscribers ----------
def post_derive_with_subscribers(analytics: Dict[str, Any], subscribers: int | None) -> Dict[str, Any]:
    """
    Add additional derived metrics that require subscriberCount, such as:

    - view_efficiency_mean: average views per video relative to subscriber base
    - channel_activity_score: frequency scaled into a simple 0..1-ish score
    - channel_trust_score: heuristic multiplier (0.5–2.0) for ML sampling weight
    """
    subs = subscribers or 0
    uploads = analytics.get("recentUploadCount_30d") or 0
    avg_views = analytics.get("avg_views_30d") or 0.0

    out: Dict[str, Any] = {}
    if subs > 0 and uploads > 0:
        eff = (avg_views * uploads) / max(subs, 1)
        out["view_efficiency_mean"] = float(eff)
    else:
        out["view_efficiency_mean"] = None

    # Very simple activity score: more uploads → closer to 1, capped
    if uploads > 0:
        out["channel_activity_score"] = min(1.0, uploads / 30.0)
    else:
        out["channel_activity_score"] = 0.0

    # Trust score: start at 1.0, modestly adjusted by viral_ratio_30d and std_views_1440
    viral_ratio = analytics.get("viral_ratio_30d")
    std_views = analytics.get("std_views_1440")
    trust = 1.0
    if isinstance(viral_ratio, (int, float)):
        trust += (viral_ratio - 0.1) * 0.5
    if isinstance(std_views, (int, float)) and std_views > 0:
        trust += 0.1
    trust = max(0.5, min(2.0, trust))
    out["channel_trust_score"] = float(trust)
    return out


# ---------- Upsert helpers ----------
def derive_static(snippet: Dict[str, Any] | None, stats: Dict[str, Any] | None) -> Dict[str, Any]:
    """Return a minimal static 'stats' block that is independent from analytics."""
    snippet = snippet or {}
    stats = stats or {}

    subs = to_int_or_none(stats.get("subscriberCount"))
    view_count = to_int_or_none(stats.get("viewCount"))
    video_count = to_int_or_none(stats.get("videoCount"))

    return {
        "subscriberCount": subs,
        "viewCount": view_count,
        "videoCount": video_count,
        "country": snippet.get("country"),
        "title": snippet.get("title"),
    }


def extract_handle(custom_url: str | None) -> str | None:
    """Extract a normalized @handle from snippet.customUrl if possible."""
    if not custom_url:
        return None
    s = custom_url.strip()
    if not s:
        return None
    if s.startswith("@"):
        return s
    # If it looks like youtube.com/c/xyz or /@xyz etc, try to pull the last segment
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    if not s:
        return None
    if not s.startswith("@"):
        s = "@" + s
    return s


def upsert_channels(
    col: Collection,
    raw_api: Dict[str, Dict[str, Any]],
    analytics_map: Dict[str, Dict[str, Any]],
    dry_run: bool,
    verbose: bool,
) -> int:
    if not raw_api and not analytics_map:
        return 0

    ops: List[UpdateOne] = []
    now = now_iso()

    # Upsert the union of ids that appear in either API data or analytics
    all_ids = set(raw_api.keys()) | set(analytics_map.keys())
    for cid in all_ids:
        api_obj = raw_api.get(cid) or {}
        snippet = api_obj.get("snippet") or {}
        statistics = api_obj.get("statistics") or {}
        etag = api_obj.get("etag")

        doc_set: Dict[str, Any] = {}

        # Static/API block (optional when --no-api / --only-analytics)
        if api_obj:
            doc_set.update(
                {
                    "_id": cid,
                    "snippet": {
                        "title": snippet.get("title"),
                        "handle": extract_handle(snippet.get("customUrl")),
                        "publishedAt": snippet.get("publishedAt"),
                        "country": snippet.get("country"),
                    },
                    "stats": derive_static(snippet, statistics),
                    "etag": etag,
                }
            )
            # Also keep a top-level handle for easier indexing / unique constraints
            if doc_set["snippet"].get("handle"):
                doc_set["handle"] = doc_set["snippet"]["handle"]

        # Analytics (from DB) — always safe to set independently of the API block
        if cid in analytics_map:
            doc_set.setdefault("analytics", {})
            doc_set["analytics"].update(analytics_map[cid])

        # Post-derivation step (requires subscriberCount for efficiency/trust metrics)
        subs_for_eff = None
        if "stats" in doc_set and isinstance(doc_set["stats"], dict):
            subs_for_eff = doc_set["stats"].get("subscriberCount")
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
                ensure_ascii=False,
            )[:160]
            print(f"[upsert.prepare] {cid} :: {preview}")

        ops.append(UpdateOne({"_id": cid}, {"$set": doc_set}, upsert=True))

    if dry_run:
        print(f"[dry-run] would upsert {len(ops)} channels; sample={list(all_ids)[:5]}")
        return 0

    if ops:
        res = col.bulk_write(ops, ordered=False)
        if verbose:
            print(
                f"[upsert] matched={res.matched_count} "
                f"modified={res.modified_count} "
                f"upserted={res.upserted_count}"
            )
        return (res.upserted_count or 0) + (res.modified_count or 0)
    return 0


# ---------- CLI ----------
def parse_args(argv: List[str]):
    import argparse

    p = argparse.ArgumentParser(description="Backfill channels (v3)")

    p.add_argument(
        "--videos-since-hours",
        type=int,
        default=72,
        help="Only consider channels from videos newer than N hours (default: 72). Set 0 to ignore.",
    )
    p.add_argument(
        "--stale-hours",
        type=int,
        default=48,
        help="Only refresh channels whose last_checked_at is older than N hours (default: 48). Set 0 to ignore.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=50000,
        help="Max channels to process in this run (default: 50000). Use 0 for unlimited.",
    )
    p.add_argument(
        "--scan-all-channels",
        action="store_true",
        help="Refresh from the entire 'channels' collection instead of discovering via 'videos'.",
    )
    p.add_argument("--dry-run", action="store_true", help="Do not write to DB; print summary only")
    p.add_argument("--verbose", action="store_true", help="Verbose logs")
    p.add_argument("--loop-until-empty", action="store_true", help="Keep looping until no more channels picked")

    # Analytics-related options
    p.add_argument(
        "--window-days",
        type=int,
        default=30,
        help="Rolling window in days for analytics (default: 30).",
    )
    p.add_argument(
        "--processed-collection",
        type=str,
        default="processed",
        help="Collection name for processed docs (default: processed).",
    )
    p.add_argument(
        "--videos-collection",
        type=str,
        default="videos",
        help="Collection name for videos (default: videos).",
    )
    p.add_argument(
        "--only-analytics",
        action="store_true",
        help="Compute & upsert analytics from DB only (skip YouTube API fetch).",
    )
    p.add_argument(
        "--no-api",
        action="store_true",
        help="Alias to skip YouTube API fetch in this run (same as --only-analytics).",
    )
    return p.parse_args(argv)


# ---------- Main ----------
def main(argv: List[str]) -> int:
    if not MONGO_URI:
        print("[ERROR] MONGO_URI not set.", file=sys.stderr)
        return EXIT_ERR

    client = MongoClient(MONGO_URI)
    db = client.get_database()

    args = parse_args(argv)

    if not API_KEY and not (args.only_analytics or args.no_api):
        print("[ERROR] YT_API_KEY not set and API fetch is enabled.", file=sys.stderr)
        return EXIT_NO_KEY

    loop = True
    total_changed = 0
    round_idx = 0

    while loop:
        round_idx += 1
        if args.scan_all_channels:
            chan_ids = pick_from_channels(db, args.stale_hours, args.limit, args.verbose)
        else:
            chan_ids = pick_from_videos(db, args.videos_since_hours, args.stale_hours, args.limit, args.verbose)

        if not chan_ids:
            if args.verbose:
                print("[main] no channelIds picked; exiting loop.")
            break

        # Analytics first (DB only)
        analytics_map = compute_channel_analytics(
            db,
            chan_ids,
            processed_col=args.processed_collection,
            videos_col=args.videos_collection,
            window_days=args.window_days,
        )

        # YT API fetch (optional)
        raw_api: Dict[str, Dict[str, Any]] = {}
        if not (args.only_analytics or args.no_api):
            yt = YTClient(API_KEY)
            raw_api = fetch_channels(yt, chan_ids)

        changed = upsert_channels(db.channels, raw_api, analytics_map, args.dry_run, args.verbose)
        total_changed += changed

        print(
            f"[round {round_idx}] picked={len(chan_ids)} "
            f"fetched={len(raw_api)} changed={changed} (total={total_changed})"
        )

        if not args.loop_until_empty:
            break

    if len(chan_ids) > 0 and not raw_api and not (args.only_analytics or args.no_api):
        print(
            "[hint] Picked > 0 but fetched = 0 → check YT_API_KEY env or channelId format. "
            "Use --verbose to inspect a sample.",
            file=sys.stderr,
        )

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
