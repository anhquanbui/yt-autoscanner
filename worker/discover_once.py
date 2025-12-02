# worker/discover_once.py (v5.1) — VIDEO DISCOVERY (3-layer: ENV + function + CLI)
# -------------------------------------------------------------------------------
# CHANGELOG (v5.1):
#   - Initialize new ml_flags.viral_v2 schema for newly discovered videos:
#       ml_flags: {
#         viral_v2: {
#           model_version, label_rule_version,
#           h6: {...}, h12: {...}, h24_validation: {...}, final: {...}
#         },
#         low_quality_v1_3h: {...},
#         low_quality_v3_6h: {...}
#       }
#
#   - viral_v2 thresholds (6h / 12h / 24h) are loaded from .env:
#       VIRAL_V2_MODEL_VERSION
#       VIRAL_V2_LABEL_RULE_VERSION
#       VIRAL_V2_THRESH_6H_PROBA,  VIRAL_V2_THRESH_6H_100
#       VIRAL_V2_THRESH_12H_PROBA, VIRAL_V2_THRESH_12H_100
#       VIRAL_V2_THRESH_24H_PROBA, VIRAL_V2_THRESH_24H_100
#
# All other behavior stays the same as v5.0.

from __future__ import annotations

import os
import sys
import io
import re
import random
import argparse
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple

import requests
from pymongo import MongoClient, UpdateOne

from config.env import load_env, get_env  # ✅ shared env loader

# ----- Console UTF-8 (Windows-safe) -----
# Ensure stdout/stderr always use UTF-8 so YouTube titles with
# non-ASCII characters won't break printing on Windows/other shells.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Load env once (search order: ~/.env → project/.env → subdirs)
load_env()

# ---- Core config (API + Mongo) ----
API_KEY   = get_env("YT_API_KEY")
MONGO_URI = get_env("MONGO_URI", "mongodb://localhost:27017/ytscan")

# Special exit code to signal "quota exhausted" to systemd/cron
EXIT_QUOTA = 88

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# ======= VIRAL V2 CONFIG (from .env) =======
# Model + rule versions (for tracking experiments / upgrades)
VIRAL_V2_MODEL_VERSION      = int(get_env("VIRAL_V2_MODEL_VERSION", "1"))
VIRAL_V2_LABEL_RULE_VERSION = int(get_env("VIRAL_V2_LABEL_RULE_VERSION", "1"))

# Thresholds for 6h / 12h / 24h stages, both probability and 0–100 scale.
# These are used when initializing ml_flags for newly-discovered videos.
VIRAL_V2_THRESH_6H_PROBA = float(get_env("VIRAL_V2_THRESH_6H_PROBA", "0.60"))
VIRAL_V2_THRESH_6H_100   = int(get_env("VIRAL_V2_THRESH_6H_100", "60"))

VIRAL_V2_THRESH_12H_PROBA = float(get_env("VIRAL_V2_THRESH_12H_PROBA", "0.70"))
VIRAL_V2_THRESH_12H_100   = int(get_env("VIRAL_V2_THRESH_12H_100", "70"))

VIRAL_V2_THRESH_24H_PROBA = float(get_env("VIRAL_V2_THRESH_24H_PROBA", "0.80"))
VIRAL_V2_THRESH_24H_100   = int(get_env("VIRAL_V2_THRESH_24H_100", "80"))
# 24h is used mainly for validation; the exact threshold has less impact on the pipeline.

# Duration parsing regex for ISO 8601 durations (e.g. PT5M30S)
_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", re.I)


# ----- Worker run logging (Mongo) -----
def log_worker_run(worker_name: str, extra: dict | None = None) -> None:
    """
    Upsert a single document in `worker_runs` to record the last time
    a worker finished (success or failure).

    This is used by the dashboard to show "last run" and detect stale workers.
    """
    try:
        load_env()

        mongo_uri = get_env("MONGO_URI", "mongodb://localhost:27017/ytscan")
        db_name_env = get_env("MONGO_DB")

        if db_name_env:
            db_name = db_name_env
        else:
            # Fallback: derive DB name from URI tail (e.g. .../ytscan?param=...)
            tail = mongo_uri.rsplit("/", 1)[-1]
            db_name = tail.split("?", 1)[0] or "ytscan"

        client = MongoClient(mongo_uri)
        db = client[db_name]

        payload: Dict[str, Any] = {
            "name": worker_name,
            "last_run": datetime.now(timezone.utc),
        }
        if extra:
            payload.update(extra)

        db.worker_runs.update_one({"name": worker_name}, {"$set": payload}, upsert=True)
    except Exception as e:
        # Logging failure is non-fatal for discovery
        print(f"[WARN] Failed to log worker run for {worker_name}: {e}", file=sys.stderr)


# ----- Helper: weighted pool parsing -----
def parse_weighted_pool(val: str) -> Tuple[List[str], List[float]]:
    """
    Parse a comma-separated weighted list like: "short:1,medium:2,long:0.5".

    Returns:
        (choices, weights) where both lists are aligned.
        Invalid weights fall back to 1.0.
    """
    if not val:
        return [], []
    choices: List[str] = []
    weights: List[float] = []
    for raw in val.split(","):
        item = raw.strip()
        if not item:
            continue
        if ":" in item:
            term, w = item.split(":", 1)
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


def pick_query_for_region(region_code: str, global_query_pool: str) -> Optional[str]:
    """
    Pick a random keyword for a given region, using:
      1) Region-specific pool: YT_RANDOM_QUERY_POOL_<REGION>
      2) Fallback global pool: YT_RANDOM_QUERY_POOL

    This allows different keyword distributions per region (e.g. JP vs US).
    """
    env_name = f"YT_RANDOM_QUERY_POOL_{region_code.upper()}"
    val = (get_env(env_name, "") or "").strip()
    if not val:
        val = (global_query_pool or "").strip()
    choices, weights = parse_weighted_pool(val)
    if not choices:
        return None

    try:
        return random.choices(choices, weights=weights, k=1)[0]
    except Exception:
        return random.choice(choices)


# ----- Duration helpers -----
def iso8601_to_seconds(s: Optional[str]) -> Optional[int]:
    """
    Convert ISO 8601 duration (e.g. 'PT5M30S') into seconds.
    Returns None if parsing fails.
    """
    if not s:
        return None
    m = _DUR_RE.match(s)
    if not m:
        return None
    h, mnt, sec = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mnt * 60 + sec


def bucket_from_seconds(secs: Optional[int]) -> Optional[str]:
    """
    Map duration in seconds to a length bucket:
      - < 4 minutes  → "short"
      - 4–20 minutes → "medium"
      - > 20 minutes → "long"
    """
    if secs is None:
        return None
    if secs < 4 * 60:
        return "short"
    if secs <= 20 * 60:
        return "medium"
    return "long"


def pick_duration_param(mode: str, pool: str) -> Optional[str]:
    """
    Decide which duration to request from the YouTube Search API.

    - If mode == "mix", we sample from a weighted pool (short/medium/long).
    - If mode is one of [short, medium, long], use it directly.
    - Otherwise, return None → API will not filter by duration.
    """
    mode = (mode or "any").lower()
    if mode == "mix":
        choices, weights = parse_weighted_pool(pool)
        if not choices:
            return None
        try:
            pick = random.choices(choices, weights=weights, k=1)[0]
        except Exception:
            pick = random.choice(choices)
        return pick if pick in {"short", "medium", "long"} else None

    if mode in {"short", "medium", "long"}:
        return mode

    return None


# ----- YouTube API helpers -----
def search_page(
    published_after_iso: str,
    region_code: str,
    query_str: Optional[str],
    video_duration: Optional[str],
    page_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Call the YouTube Search API for a single page of results.

    This is the "discovery" endpoint (search by date / keyword / region).
    """
    if not API_KEY:
        raise RuntimeError("Missing YT_API_KEY")

    params: Dict[str, Any] = {
        "key": API_KEY,
        "part": "snippet",
        "type": "video",
        "order": "date",          # newest first
        "maxResults": 50,
        "regionCode": region_code,
        "publishedAfter": published_after_iso,
    }
    if query_str:
        params["q"] = query_str
    if video_duration in {"short", "medium", "long"}:
        params["videoDuration"] = video_duration
    if page_token:
        params["pageToken"] = page_token

    r = requests.get(SEARCH_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def videos_details(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Fetch snippet + contentDetails for a list of video IDs via the Videos API.

    We use this to enrich:
      - categoryId
      - duration (ISO + seconds + length bucket)
    Returns a mapping: videoId -> {snippet, contentDetails}
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not video_ids:
        return out
    if not API_KEY:
        raise RuntimeError("Missing YT_API_KEY")

    batched = [video_ids[i : i + 50] for i in range(0, len(video_ids), 50)]
    for batch in batched:
        params = {
            "key": API_KEY,
            "part": "snippet,contentDetails",
            "id": ",".join(batch),
        }
        r = requests.get(VIDEOS_URL, params=params, timeout=30)
        r.raise_for_status()
        for it in r.json().get("items", []):
            vid = it.get("id")
            if vid:
                out[vid] = {
                    "snippet": it.get("snippet", {}) or {},
                    "contentDetails": it.get("contentDetails", {}) or {},
                }
    return out


# ----- Mongo upsert -----
def upsert_minimal(
    items: List[Dict[str, Any]],
    db,
    region_used: str,
    query_used: Optional[str],
    random_mode: bool,
) -> int:
    """
    Upsert minimal video documents into the `videos` collection.

    For each video, we initialize:
      - source: query, regionCode, randomMode
      - snippet: title, thumbnails, channelId, categoryId, durationISO, durationSec, lengthBucket
      - tracking: status=tracking, discovered_at, next_poll_after, etc.
      - ml_flags:
          - viral_v2 (nested: h6, h12, h24_validation, final)
          - low_quality_v1_3h
          - low_quality_v3_6h

    Upsert strategy:
      - `$setOnInsert` for heavy/static fields (source/tracking/ml_flags/stats_snapshots)
      - `$set` for snippet → always refresh basic metadata.
    """
    ops: List[UpdateOne] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for it in items:
        vid = it.get("id", {}).get("videoId")
        sn = it.get("snippet", {}) or {}
        if not vid or not sn:
            continue

        # ===== Default ML flags for viral_v2 + low_quality models =====
                # ===== Default ML flags for viral_v2 + low_quality models =====
        ml_flags = {
            "viral_v2": {
                "model_version": VIRAL_V2_MODEL_VERSION,
                "label_rule_version": VIRAL_V2_LABEL_RULE_VERSION,

                # 6h stage: early candidate detection (multiclass)
                "h6": {
                    "score_proba": None,
                    "score_100": None,
                    "is_candidate": None,
                    "threshold_proba": VIRAL_V2_THRESH_6H_PROBA,
                    "threshold_100": VIRAL_V2_THRESH_6H_100,
                    "evaluated_at": None,

                    # Multiclass extras
                    "top_class_idx": None,   # 0=non_viral, 1=weak_viral, 2=viral, 3=super_viral
                    "top_class": None,       # "non_viral" | "weak_viral" | "viral" | "super_viral"
                    "proba_non": None,
                    "proba_weak": None,
                    "proba_viral": None,
                    "proba_super": None,
                },

                # 12h stage: viral confirmation (multiclass)
                "h12": {
                    "score_proba": None,
                    "score_100": None,
                    "is_viral_12h": None,
                    "threshold_proba": VIRAL_V2_THRESH_12H_PROBA,
                    "threshold_100": VIRAL_V2_THRESH_12H_100,
                    "evaluated_at": None,

                    # Multiclass extras
                    "top_class_idx": None,
                    "top_class": None,
                    "proba_non": None,
                    "proba_weak": None,
                    "proba_viral": None,
                    "proba_super": None,
                },

                # 24h stage: validation of previous decision (multiclass)
                "h24_validation": {
                    "score_proba": None,
                    "score_100": None,
                    "threshold_proba": VIRAL_V2_THRESH_24H_PROBA,
                    "threshold_100": VIRAL_V2_THRESH_24H_100,
                    "evaluated_at": None,

                    # Multiclass extras
                    "top_class_idx": None,
                    "top_class": None,
                    "proba_non": None,
                    "proba_weak": None,
                    "proba_viral": None,
                    "proba_super": None,
                },

                # Final decision summary across stages (used by viral_finalize)
                "final": {
                    # Final label:
                    #   "unknown" | "non_viral" | "weak_viral" | "viral" | "super_viral" | "non_viral_lowq"
                    "status": "unknown",
                    # Which stage decided:
                    #   "6h" | "12h" | "24h" | "low_quality"
                    "decided_stage": None,

                    # Final “any viral” probability & score at decision stage
                    "score_proba": None,
                    "score_100": None,
                    "threshold_proba": None,
                    "threshold_100": None,
                    "decided_at": None,
                    "reason": None,

                    # Trajectory metadata (for dashboard / debug)
                    "top_class_6h": None,
                    "top_class_12h": None,
                    "top_class_24h": None,
                    "score_proba_6h": None,
                    "score_proba_12h": None,
                    "score_proba_24h": None,
                },
            },

            # Low-quality models (kept same as previous versions)
            "low_quality_v1_3h": {
                "is_low": False,
                "score": 0.0,
                "threshold": None,
                "updated_at": None,
            },
            "low_quality_v3_6h": {
                "is_low": False,
                "score": 0.0,
                "threshold": None,
                "updated_at": None,
            },
        }

        # Full doc template for NEW videos
        full_doc = {
            "_id": vid,
            "source": {
                "query": query_used,
                "regionCode": region_used,
                "randomMode": bool(random_mode),
            },
            "snippet": {
                "title": sn.get("title"),
                "publishedAt": sn.get("publishedAt"),
                "thumbnails": sn.get("thumbnails", {}),
                "channelId": sn.get("channelId"),
                "categoryId": sn.get("categoryId"),
                "durationISO": sn.get("durationISO"),
                "durationSec": sn.get("durationSec"),
                "lengthBucket": sn.get("lengthBucket"),
            },
            "tracking": {
                "status": "tracking",
                "discovered_at": now_iso,
                "last_polled_at": None,
                "next_poll_after": now_iso,
                "poll_count": 0,
                "stop_reason": None,
            },
            "stats_snapshots": [],
            "ml_flags": ml_flags,
        }

        # We don't want to overwrite snippet inside $setOnInsert so we pop it out
        insert_doc = full_doc.copy()
        insert_doc.pop("snippet", None)

        update_doc = {
            # Heavy, mostly-static fields only on first insert
            "$setOnInsert": insert_doc,
            # Always refresh snippet on each discovery run
            "$set": {"snippet": full_doc["snippet"]},
        }
        ops.append(UpdateOne({"_id": vid}, update_doc, upsert=True))

    if not ops:
        return 0

    res = db.videos.bulk_write(ops, ordered=False)
    # upserted_count counts only *new* documents inserted
    return int(res.upserted_count or 0)


# ======================================================================
#  CORE FUNCTION (layer 1): run_discover(...)
# ======================================================================
def run_discover(
    *,
    region: Optional[str] = None,
    query: Optional[str] = None,
    random_pick: Optional[bool] = None,
    since_minutes: Optional[int] = None,
    max_pages: Optional[int] = None,
    duration_mode: Optional[str] = None,
    duration_pool: Optional[str] = None,
    exclude_live: Optional[bool] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Core discovery logic. This can be called directly from Python code
    (e.g. Streamlit dashboard) without going through CLI or environment.

    It reads defaults from env, applies per-call overrides, then:
      - Defines a "near-now" time window (publishedAfter).
      - Calls YouTube Search API page by page.
      - Enriches categoryId + duration via Videos API.
      - Filters out live/upcoming if configured.
      - Upserts videos into MongoDB with initial tracking + ml_flags.
    """
    load_env()

    # --- Read env defaults (used if user does not override) ---
    env_region = get_env("YT_REGION", "US")
    env_query = get_env("YT_QUERY", "money")

    env_random_pick = (get_env("YT_RANDOM_PICK", "0") or "0").lower() in ("1", "true", "yes")
    env_random_region_pool_raw = get_env("YT_RANDOM_REGION_POOL", "") or ""

    env_since_minutes = int(get_env("YT_SINCE_MINUTES", "20"))
    env_max_pages = int(get_env("YT_MAX_PAGES", "1"))

    env_duration_mode = (get_env("YT_DURATION_MODE", "any") or "any").lower()
    env_duration_pool = get_env("YT_DURATION_POOL", "short:1,medium:1,long:1,any:0") or "short:1,medium:1,long:1,any:0"

    env_exclude_live = (get_env("YT_EXCLUDE_LIVE", "1") or "1").lower() in ("1", "true", "yes")

    global_query_pool = get_env("YT_RANDOM_QUERY_POOL", "") or ""

    # --- Effective values: env defaults overridden by function arguments ---
    region_used = (region or env_region).upper()
    since_used = since_minutes if since_minutes is not None else env_since_minutes
    max_pages_used = max_pages if max_pages is not None else env_max_pages

    duration_mode_used = (duration_mode or env_duration_mode).lower()
    duration_pool_used = duration_pool or env_duration_pool

    random_mode = env_random_pick if random_pick is None else bool(random_pick)
    exclude_live_used = env_exclude_live if exclude_live is None else bool(exclude_live)

    # Pool of regions for random-mode (if enabled)
    random_region_pool = [
        x.strip().upper()
        for x in env_random_region_pool_raw.split(",")
        if x.strip()
    ]

    if not API_KEY:
        if verbose:
            print("Missing YT_API_KEY", file=sys.stderr)
        return {
            "exit_code": 2,
            "pages": 0,
            "total_found": 0,
            "total_upserted": 0,
            "region_used": region_used,
            "query_used": None,
            "duration_used": None,
            "random_mode": random_mode,
        }

    # If random region is enabled and a pool is configured, pick one region at random
    if random_mode and random_region_pool:
        region_used = random.choice(random_region_pool)

    # --- Pick query (random vs fixed) ---
    query_used: Optional[str] = None
    if random_mode:
        # If a query is explicitly provided, honor it even in random mode
        if query:
            query_used = query
        else:
            query_used = pick_query_for_region(region_used, global_query_pool)

    if not query_used:
        # Fallback: explicit override or env default
        query_used = query or env_query

    # --- Duration parameter for YouTube search ---
    duration_used = pick_duration_param(duration_mode_used, duration_pool_used)

    # --- DB client / database selection ---
    client = MongoClient(MONGO_URI)
    db = client.get_database()

    # --- Define "near-now" time window for search ---
    now = datetime.now(timezone.utc)
    published_after = (now - timedelta(minutes=since_used)).isoformat()

    if verbose:
        print(">>> discover_once SCAN-ONLY (near-now + categoryId + duration filter) starting")
        print(
            f"Near-now slice: {published_after}..(now) | "
            f"region={region_used} | query={query_used!r} | random={random_mode} | "
            f"duration={duration_used or 'any'} | exclude_live={exclude_live_used}"
        )

    page_token: Optional[str] = None
    pages = 0
    total_found = 0
    total_upserted = 0

    try:
        # Main page-loop over YouTube Search API
        while True:
            if pages >= max_pages_used:
                if verbose:
                    print(f"Reached YT_MAX_PAGES={max_pages_used}, stop.")
                break
            pages += 1

            data = search_page(
                published_after_iso=published_after,
                region_code=region_used,
                query_str=query_used,
                video_duration=duration_used,
                page_token=page_token,
            )

            items = data.get("items", []) or []
            found = len(items)
            filtered_live = 0

            # Optionally remove live / upcoming content (VOD-only behavior)
            if exclude_live_used and found > 0:
                before = len(items)
                items = [
                    it
                    for it in items
                    if (it.get("snippet", {}).get("liveBroadcastContent") or "none").lower()
                    == "none"
                ]
                filtered_live = before - len(items)
                if verbose and filtered_live:
                    print(f"[page {pages}] filtered_live={filtered_live}")

            total_found += len(items)

            # If we got items, enrich them with categoryId + duration via Videos API
            if items:
                ids = [
                    it.get("id", {}).get("videoId")
                    for it in items
                    if it.get("id", {}).get("videoId")
                ]
                det_map = videos_details(ids)
                enriched_cate = 0
                enriched_dur = 0

                for it in items:
                    vid = it.get("id", {}).get("videoId")
                    if not vid:
                        continue

                    sn = it.get("snippet", {}) or {}
                    det = det_map.get(vid) or {}
                    sn2 = det.get("snippet", {}) or {}
                    cd = det.get("contentDetails", {}) or {}

                    # Merge category from detail call
                    cate = sn2.get("categoryId")
                    if cate:
                        sn["categoryId"] = cate
                        enriched_cate += 1

                    # Merge duration & derived features
                    dur_iso = cd.get("duration")
                    secs = iso8601_to_seconds(dur_iso) if dur_iso else None
                    if dur_iso:
                        sn["durationISO"] = dur_iso
                    if secs is not None:
                        sn["durationSec"] = secs
                        sn["lengthBucket"] = bucket_from_seconds(secs)
                        enriched_dur += 1

                    it["snippet"] = sn

                if verbose:
                    print(
                        f"[page {pages}] found={found}, enriched_category={enriched_cate}, "
                        f"enriched_duration={enriched_dur}"
                    )

            # Upsert into Mongo
            up = upsert_minimal(
                items=items,
                db=db,
                region_used=region_used,
                query_used=query_used,
                random_mode=random_mode,
            )
            total_upserted += up

            if verbose:
                # Print a small sample for human sanity check
                for it in items[:5]:
                    vid = it.get("id", {}).get("videoId")
                    sn = it.get("snippet", {}) or {}
                    print(
                        f" - {vid} | {sn.get('publishedAt')} | "
                        f"len={sn.get('lengthBucket')} | cate={sn.get('categoryId')} | "
                        f"{sn.get('title')}"
                    )

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        if verbose:
            print(
                f">>> DONE. pages={pages}, total_found={total_found}, "
                f"total_upserted={total_upserted}"
            )

        # Log success to worker_runs
        log_worker_run(
            "discover_once",
            {
                "status": "ok",
                "pages": pages,
                "total_found": total_found,
                "total_upserted": total_upserted,
            },
        )

        return {
            "exit_code": 0,
            "pages": pages,
            "total_found": total_found,
            "total_upserted": total_upserted,
            "region_used": region_used,
            "query_used": query_used,
            "duration_used": duration_used,
            "random_mode": random_mode,
        }

    except requests.HTTPError as e:
        # Handle HTTP errors from YouTube API (including quota issues)
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

        status_code = getattr(e.response, "status_code", None)
        if status_code == 403 and str(reason) in {
            "quotaExceeded",
            "dailyLimitExceeded",
            "rateLimitExceeded",
            "userRateLimitExceeded",
        }:
            # Special case: YouTube quota exhausted → use EXIT_QUOTA code
            if verbose:
                print("YouTube quota exhausted — update YT_API_KEY.", file=sys.stderr)
            log_worker_run(
                "discover_once",
                {"status": "quota_exhausted", "reason": str(reason)},
            )
            return {
                "exit_code": EXIT_QUOTA,
                "pages": pages,
                "total_found": total_found,
                "total_upserted": total_upserted,
                "region_used": region_used,
                "query_used": query_used,
                "duration_used": duration_used,
                "random_mode": random_mode,
            }

        # Other HTTP errors: log and return generic error exit code
        if verbose:
            print("YouTube API error:", body, file=sys.stderr)
        log_worker_run(
            "discover_once",
            {"status": "error_http", "reason": str(reason)},
        )
        return {
            "exit_code": 1,
            "pages": pages,
            "total_found": total_found,
            "total_upserted": total_upserted,
            "region_used": region_used,
            "query_used": query_used,
            "duration_used": duration_used,
            "random_mode": random_mode,
        }

    except Exception as e:
        # Catch-all for unexpected runtime errors
        if verbose:
            print("Error:", e, file=sys.stderr)
        log_worker_run(
            "discover_once",
            {"status": "error", "error": str(e)},
        )
        return {
            "exit_code": 1,
            "pages": pages,
            "total_found": total_found,
            "total_upserted": total_upserted,
            "region_used": region_used,
            "query_used": query_used,
            "duration_used": duration_used,
            "random_mode": random_mode,
        }


# ======================================================================
#  CLI LAYER (layer 3): argparse wrapper around run_discover(...)
# ======================================================================
def build_arg_parser() -> argparse.ArgumentParser:
    """
    Build argparse.ArgumentParser for CLI usage.

    Layer design:
      - Layer 1: run_discover(...) — pure Python function
      - Layer 2: (env) — default configuration from environment
      - Layer 3: CLI — overrides via command-line arguments
    """
    parser = argparse.ArgumentParser(
        prog="discover_once",
        description=(
            "Near-now YouTube video discovery worker "
            "(categoryId + duration enrichment, VOD-only by default)."
        ),
    )
    parser.add_argument("--region", help="Override YT_REGION (e.g. US, JP, VN).")
    parser.add_argument("--query", help="Override YT_QUERY / random query pool result.")
    parser.add_argument(
        "--since-minutes",
        type=int,
        help="Override YT_SINCE_MINUTES (near-now window in minutes).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Override YT_MAX_PAGES (YouTube Search pages, 1 page = 50 results).",
    )
    parser.add_argument(
        "--duration-mode",
        choices=["any", "short", "medium", "long", "mix"],
        help="Override YT_DURATION_MODE.",
    )
    parser.add_argument(
        "--duration-pool",
        help="Override YT_DURATION_POOL (for mix mode, e.g. 'short:1,medium:2,long:1').",
    )
    parser.add_argument(
        "--exclude-live",
        type=int,
        choices=[0, 1],
        help="Override YT_EXCLUDE_LIVE (1=exclude live/upcoming, 0=include).",
    )
    parser.add_argument(
        "--random-pick",
        action="store_true",
        help="Force random pick mode ON (overrides YT_RANDOM_PICK).",
    )
    parser.add_argument(
        "--no-random-pick",
        action="store_true",
        help="Force random pick mode OFF (overrides YT_RANDOM_PICK).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose logging (only errors are printed).",
    )
    return parser


def main() -> int:
    """
    CLI entrypoint: parse arguments, resolve overrides, and call run_discover().
    """
    parser = build_arg_parser()
    args = parser.parse_args()

    # Resolve random_pick tri-state: True / False / None (use env)
    if args.random_pick and args.no_random_pick:
        # If both are passed, default to False to avoid ambiguous behavior
        random_pick: Optional[bool] = False
    elif args.random_pick:
        random_pick = True
    elif args.no_random_pick:
        random_pick = False
    else:
        random_pick = None

    # Convert exclude_live CLI int into Optional[bool]
    exclude_live: Optional[bool] = None
    if args.exclude_live is not None:
        exclude_live = bool(args.exclude_live)

    info = run_discover(
        region=args.region,
        query=args.query,
        random_pick=random_pick,
        since_minutes=args.since_minutes,
        max_pages=args.max_pages,
        duration_mode=args.duration_mode,
        duration_pool=args.duration_pool,
        exclude_live=exclude_live,
        verbose=not args.quiet,
    )

    # Make sure exit_code is always an int so systemd/cron can interpret it
    return int(info.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
