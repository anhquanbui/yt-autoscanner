# worker/discover_once.py (v5.0) — VIDEO DISCOVERY (3-layer: ENV + function + CLI)
# ------------------------------------------------------------------------------
# CHANGELOG (v5.0):
#   - Refactored into 3 layers:
#       * Core function: run_discover(...)
#       * Environment-based defaults (YT_* env vars)
#       * Optional CLI interface via argparse
#   - Uses config.env (load_env/get_env) for consistent .env loading.
#   - Kept the same behavior for:
#       * near-now scan
#       * duration & category enrichment
#       * exclude live/upcoming
#       * ml_flags initialization (viral + low_quality 3h/6h)
#
# NOTE:
#   - Can now be used in three ways:
#       1) ENV-only (systemd, cron, PowerShell/batch)
#       2) CLI overrides (e.g. --region, --duration-mode, --since-minutes)
#       3) Direct Python call via run_discover(...) (e.g. from Streamlit dashboard)

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
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    
# Load env once (priority: ~/.env → project/.env → subdirs)
load_env()

# ---- Config ----
API_KEY   = get_env("YT_API_KEY")
MONGO_URI = get_env("MONGO_URI", "mongodb://localhost:27017/ytscan")

EXIT_QUOTA = 88

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# Duration parsing regex
_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", re.I)


# ----- Worker run logging (Mongo) -----
def log_worker_run(worker_name: str, extra: dict | None = None) -> None:
    """
    Upsert a single document in `worker_runs` to record the last time
    a worker finished (success or failure).

    This is kept lightweight and best-effort: failures are printed but do not
    crash the worker.
    """
    try:
        # Ensure .env is loaded (idempotent, no-op if already loaded)
        load_env()

        mongo_uri = get_env("MONGO_URI", "mongodb://localhost:27017/ytscan")
        db_name_env = get_env("MONGO_DB")

        # Detect DB name: prefer MONGO_DB, otherwise parse from URI
        if db_name_env:
            db_name = db_name_env
        else:
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
        print(f"[WARN] Failed to log worker run for {worker_name}: {e}", file=sys.stderr)

# ----- Helper: weighted pool parsing -----
def parse_weighted_pool(val: str) -> Tuple[List[str], List[float]]:
    """
    Parse a comma-separated weighted list like: "short:1,medium:2,long:0.5".

    Returns:
        choices: list of terms (e.g. ["short", "medium", "long"])
        weights: list of float weights (same length as choices)
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
    Decide which duration to request from the YouTube Search API:
      - 'short', 'medium', 'long' → use as-is
      - 'any' or unknown         → None (no filter)
      - 'mix'                    → randomly pick from weighted pool
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
    """
    if not API_KEY:
        raise RuntimeError("Missing YT_API_KEY")

    params: Dict[str, Any] = {
        "key": API_KEY,
        "part": "snippet",
        "type": "video",
        "order": "date",
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
    Initializes:
      - source: query, regionCode, randomMode
      - snippet: title, thumbnails, channelId, categoryId, durationISO, durationSec, lengthBucket
      - tracking: status=tracking, discovered_at, next_poll_after, etc.
      - ml_flags: viral_v1, low_quality_v1_3h, low_quality_v3_6h
    """
    ops: List[UpdateOne] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for it in items:
        vid = it.get("id", {}).get("videoId")
        sn = it.get("snippet", {}) or {}
        if not vid or not sn:
            continue

        # Default ML flags (unified schema)
        ml_flags = {
            "viral_v1": {
                "likely": False,
                "confirmed": False,
                "score": 0.0,
                "updated_at": None,
            },
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

        # Upsert: set heavy fields only on insert, but always refresh snippet
        insert_doc = full_doc.copy()
        insert_doc.pop("snippet", None)
        update_doc = {
            "$setOnInsert": insert_doc,
            "$set": {"snippet": full_doc["snippet"]},
        }
        ops.append(UpdateOne({"_id": vid}, update_doc, upsert=True))

    if not ops:
        return 0

    res = db.videos.bulk_write(ops, ordered=False)
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

    All arguments are optional. If not provided, they fall back to
    environment variables and then to hard-coded defaults.

    Returns:
        A dictionary with:
          - exit_code: int (0, EXIT_QUOTA, or 1/2 for errors)
          - pages: int
          - total_found: int
          - total_upserted: int
          - region_used: str
          - query_used: Optional[str]
          - duration_used: Optional[str]
          - random_mode: bool
    """
    # Ensure environment is loaded (idempotent)
    load_env()

    # --- Read env defaults ---
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

    # --- Effective values (CLI/function overrides > ENV > defaults) ---
    region_used = (region or env_region).upper()
    since_used = since_minutes if since_minutes is not None else env_since_minutes
    max_pages_used = max_pages if max_pages is not None else env_max_pages

    duration_mode_used = (duration_mode or env_duration_mode).lower()
    duration_pool_used = duration_pool or env_duration_pool

    random_mode = (
        env_random_pick if random_pick is None else bool(random_pick)
    )
    exclude_live_used = (
        env_exclude_live if exclude_live is None else bool(exclude_live)
    )

    random_region_pool = [
        x.strip().upper()
        for x in env_random_region_pool_raw.split(",")
        if x.strip()
    ]

    # --- API key guard ---
    if not API_KEY:
        if verbose:
            print("Missing YT_API_KEY", file=sys.stderr)
        # Use exit_code=2 for missing API key
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

    # --- Pick region when random_mode is enabled ---
    if random_mode and random_region_pool:
        region_used = random.choice(random_region_pool)

    # --- Pick query ---
    query_used: Optional[str] = None
    if random_mode:
        # CLI / function-specified query has highest priority in random mode
        if query:
            query_used = query
        else:
            query_used = pick_query_for_region(region_used, global_query_pool)

    if not query_used:
        # Fall back to explicit query or env query if random did not provide one
        query_used = query or env_query

    # --- Duration parameter for Search API ---
    duration_used = pick_duration_param(duration_mode_used, duration_pool_used)

    # --- DB client ---
    client = MongoClient(MONGO_URI)
    db = client.get_database()

    # --- Near-now window ---
    now = datetime.now(timezone.utc)
    published_after = (now - timedelta(minutes=since_used)).isoformat()

    if verbose:
        print(
            ">>> discover_once SCAN-ONLY (near-now + categoryId + duration filter) starting"
        )
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

            # Exclude live / upcoming if requested
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

                    # categoryId enrichment
                    cate = sn2.get("categoryId")
                    if cate:
                        sn["categoryId"] = cate
                        enriched_cate += 1

                    # duration enrichment
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
            if verbose:
                print(
                    "YouTube quota exhausted — update YT_API_KEY.",
                    file=sys.stderr,
                )
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
    Build an argparse parser for CLI usage.
    All arguments are optional and override env defaults when provided.
    """
    parser = argparse.ArgumentParser(
        prog="discover_once",
        description=(
            "Near-now YouTube video discovery worker "
            "(categoryId + duration enrichment, VOD-only by default)."
        ),
    )
    parser.add_argument(
        "--region",
        help="Override YT_REGION (e.g. US, JP, VN).",
    )
    parser.add_argument(
        "--query",
        help="Override YT_QUERY / random query pool result.",
    )
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
    CLI entry point.
    Parses arguments, calls run_discover(...), and returns an exit code.
    """
    parser = build_arg_parser()
    args = parser.parse_args()

    # Resolve random_pick tri-state: True / False / None
    random_pick: Optional[bool]
    if args.random_pick and args.no_random_pick:
        # If both flags are set, prefer explicit OFF (just to be deterministic)
        random_pick = False
    elif args.random_pick:
        random_pick = True
    elif args.no_random_pick:
        random_pick = False
    else:
        random_pick = None

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

    return int(info.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
