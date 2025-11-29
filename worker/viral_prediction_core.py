# -*- coding: utf-8 -*-
"""
worker.viral_prediction_core

Core logic for running the three viral models (6h, 12h, 24h) directly on MongoDB
and writing results into `ml_flags.viral_v2.*`.

Stages:
    - 6h  : early signal          → ml_flags.viral_v2.h6
    - 12h : confirmation          → ml_flags.viral_v2.h12
    - 24h : late validator        → ml_flags.viral_v2.h24_validation

CLI examples:

    # Run 6h early-signal model
    python -m worker.viral_prediction_core 6h

    # Run 12h confirmation model
    python -m worker.viral_prediction_core 12h

    # Run 24h validation model
    python -m worker.viral_prediction_core 24h

    # Only score videos that are still missing score_proba for that stage
    python -m worker.viral_prediction_core 6h --only-missing

    # Force recompute for all videos that meet the age condition
    # (ignores only-missing, but still respects age gating per stage)
    python -m worker.viral_prediction_core 12h --force-all
"""

from __future__ import annotations

import os
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from pandas import to_datetime
from pymongo import MongoClient
import joblib

from config.env import load_env

# Load environment variables once (shared logic with other workers)
load_env()

# orjson is optional but faster if available
try:
    import orjson as _orjson
except Exception:  # fall back to stdlib json if orjson is not installed
    _orjson = None


# ============================================================
# CONFIG
# ============================================================

# Mongo connection defaults
DEFAULT_MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")
DEFAULT_DB_NAME = os.getenv("MONGO_DB", "ytscan")
DEFAULT_COLLECTION = os.getenv("MONGO_VIDEOS_COLLECTION", "videos")

# Model paths (can be overridden via env)
MODEL_DIR = Path(os.getenv("VIRAL_MODEL_DIR", "models/viral"))

MODEL_6H_PATH = Path(os.getenv("VIRAL_MODEL_6H", MODEL_DIR / "viral_xgb_6h.joblib"))
MODEL_12H_PATH = Path(os.getenv("VIRAL_MODEL_12H", MODEL_DIR / "viral_xgb_12h.joblib"))
MODEL_24H_PATH = Path(os.getenv("VIRAL_MODEL_24H", MODEL_DIR / "viral_xgb_24h.joblib"))

# Version metadata written into ml_flags.viral_v2
MODEL_VERSION = 1
LABEL_RULE_VERSION = 1


# ============================================================
# Aggregation helpers (adapted from training 6h / 12h notebooks)
# ============================================================

def _loads_fast(s: Any) -> Any:
    """
    Fast JSON loader with a "no-op" behavior for already-parsed objects.

    Accepts:
      - dict / list           → returned as-is
      - JSON string (object / array)
      - other scalars / None  → returned as-is

    Returns:
      - Parsed Python object on valid JSON
      - None on parse failure (for both orjson and json)
    """
    if isinstance(s, (dict, list)):
        return s
    if not isinstance(s, str):
        return s
    s = s.strip()
    if not s:
        return None
    if (s[0] == "{" and s[-1] == "}") or (s[0] == "[" and s[-1] == "]"):
        try:
            if _orjson is not None:
                return _orjson.loads(s)
        except Exception:
            pass
        import json as _json

        try:
            return _json.loads(s)
        except Exception:
            return None
    return s


def _dig(d: Any, dotted_key: str) -> Any:
    """
    Safe nested dict getter using a dotted key path.

    Example:
        _dig({"a": {"b": 1}}, "a.b") -> 1
    """
    if not isinstance(d, dict):
        return None
    if dotted_key in d:
        return d[dotted_key]
    cur = d
    for k in dotted_key.split("."):
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    return cur


def _safe_int(x: Any) -> int:
    """
    Convert a value to int with robust fallback:

      - First try int(x)
      - If that fails, try int(float(x))
      - If that fails, return 0
    """
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return 0


def _first_leq(xs: List[float], ys: List[int], target: float) -> float:
    """
    Given sorted xs (timestamps in hours) and corresponding ys (values),
    return the last y such that x <= target.

    If no such point exists, returns np.nan.
    """
    out = np.nan
    for x, y in zip(xs, ys):
        if x <= target:
            out = y
        else:
            break
    return out


def aggregate_0_24h(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Aggregate stats for a single video into fixed 0–24h buckets.

    Returns a feature dict containing:
      - Raw counts: views_*, likes_*, comms_*
      - Growth ratios: g_*
      - Slopes: slope_views_*
      - Engagement rates: like_rate_*, comm_rate_*
      - durationSec, lengthBucket

    If anything is fundamentally missing (no publishedAt or no snapshots),
    returns None and the caller should skip this record.
    """
    if isinstance(rec, str):
        rec = _loads_fast(rec)
    if not isinstance(rec, dict):
        return None

    # --- Basic snippet / metadata ---
    sn = _loads_fast(rec.get("snippet"))
    if isinstance(sn, dict):
        pub = sn.get("publishedAt")
        duration = sn.get("durationSec")
        length = sn.get("lengthBucket")
    else:
        pub = _dig(rec, "snippet.publishedAt") or rec.get("publishedAt")
        duration = _dig(rec, "snippet.durationSec") or rec.get("durationSec")
        length = _dig(rec, "snippet.lengthBucket") or rec.get("lengthBucket")

    if not pub:
        return None

    t0 = to_datetime(pub, utc=True, errors="coerce")
    if t0 is None or str(t0) == "NaT":
        return None

    # --- Stats snapshots ---
    snaps = _loads_fast(rec.get("stats_snapshots")) or _loads_fast(
        rec.get("stats.snapshots")
    )
    if not isinstance(snaps, list) or not snaps:
        return None

    th: List[float] = []
    v: List[int] = []
    l: List[int] = []
    c: List[int] = []

    for s in snaps:
        s = _loads_fast(s) if isinstance(s, str) else s
        if not isinstance(s, dict):
            continue
        ts = s.get("ts") or s.get("timestamp") or s.get("time")
        if not ts:
            continue
        t = to_datetime(ts, utc=True, errors="coerce")
        if t is None or str(t) == "NaT":
            continue
        h = (t - t0).total_seconds() / 3600.0
        # Ignore clearly-invalid negative ages
        if h < -0.1:
            continue

        th.append(h)
        v.append(_safe_int(s.get("viewCount")))
        l.append(_safe_int(s.get("likeCount")))
        c.append(_safe_int(s.get("commentCount")))

    if not th:
        return None

    # Sort by time just in case snapshots are out of order
    arr = sorted(zip(th, v, l, c), key=lambda z: z[0])
    th = [a[0] for a in arr]
    v = [a[1] for a in arr]
    l = [a[2] for a in arr]
    c = [a[3] for a in arr]

    # Fixed time horizons (in hours)
    targets = [0.1667, 0.5, 1.0, 3.0, 6.0, 12.0, 24.0]
    v10, v30, v1, v3, v6, v12, v24 = [_first_leq(th, v, t) for t in targets]
    l10, l30, l1, l3, l6, l12, l24 = [_first_leq(th, l, t) for t in targets]
    c10, c30, c1, c3, c6, c12, c24 = [_first_leq(th, c, t) for t in targets]

    eps = 1e-6

    # Growth ratios
    g_10_1 = (v1 + eps) / (v10 + eps)
    g_1_3 = (v3 + eps) / (v1 + eps)
    g_3_6 = (v6 + eps) / (v3 + eps)
    g_6_12 = (v12 + eps) / (v6 + eps)
    g_12_24 = (v24 + eps) / (v12 + eps)
    g_1_6 = (v6 + eps) / (v1 + eps)
    g_6_24 = (v24 + eps) / (v6 + eps)
    g_1_24 = (v24 + eps) / (v1 + eps)

    # Slopes (approximate "velocity" over different windows)
    slope_10_1 = (v1 - v10) / max(0.8333, eps)
    slope_1_3 = (v3 - v1) / max(2.0, eps)
    slope_3_6 = (v6 - v3) / max(3.0, eps)
    slope_6_12 = (v12 - v6) / max(6.0, eps)
    slope_12_24 = (v24 - v12) / max(12.0, eps)
    slope_1_6 = (v6 - v1) / max(5.0, eps)
    slope_6_24 = (v24 - v6) / max(18.0, eps)

    # Engagement rates at different horizons
    like_rate_3h = l3 / max(v3, 1.0)
    like_rate_6h = l6 / max(v6, 1.0)
    like_rate_12h = l12 / max(v12, 1.0)
    like_rate_24h = l24 / max(v24, 1.0)

    comm_rate_3h = c3 / max(v3, 1.0)
    comm_rate_6h = c6 / max(v6, 1.0)
    comm_rate_12h = c12 / max(v12, 1.0)
    comm_rate_24h = c24 / max(v24, 1.0)

    return {
        "_id": rec.get("_id"),
        # Raw counts
        "views_10m": v10,
        "views_30m": v30,
        "views_1h": v1,
        "views_3h": v3,
        "views_6h": v6,
        "views_12h": v12,
        "views_24h": v24,
        "likes_10m": l10,
        "likes_30m": l30,
        "likes_1h": l1,
        "likes_3h": l3,
        "likes_6h": l6,
        "likes_12h": l12,
        "likes_24h": l24,
        "comms_10m": c10,
        "comms_30m": c30,
        "comms_1h": c1,
        "comms_3h": c3,
        "comms_6h": c6,
        "comms_12h": c12,
        "comms_24h": c24,
        # Growth
        "g_10m_to_1h": g_10_1,
        "g_1h_to_3h": g_1_3,
        "g_3h_to_6h": g_3_6,
        "g_6h_to_12h": g_6_12,
        "g_12h_to_24h": g_12_24,
        "g_1h_to_6h": g_1_6,
        "g_6h_to_24h": g_6_24,
        "g_1h_to_24h": g_1_24,
        # Slopes
        "slope_views_10m_to_1h": slope_10_1,
        "slope_views_1h_to_3h": slope_1_3,
        "slope_views_3h_to_6h": slope_3_6,
        "slope_views_6h_to_12h": slope_6_12,
        "slope_views_12h_to_24h": slope_12_24,
        "slope_views_1h_to_6h": slope_1_6,
        "slope_views_6h_to_24h": slope_6_24,
        # Engagement
        "like_rate_3h": like_rate_3h,
        "like_rate_6h": like_rate_6h,
        "like_rate_12h": like_rate_12h,
        "like_rate_24h": like_rate_24h,
        "comm_rate_3h": comm_rate_3h,
        "comm_rate_6h": comm_rate_6h,
        "comm_rate_12h": comm_rate_12h,
        "comm_rate_24h": comm_rate_24h,
        # Meta
        "durationSec": float(duration) if duration is not None else np.nan,
        "lengthBucket": length,
    }


# ============================================================
# Video age helper (using latest_stats_ts)
# ============================================================

def compute_video_age_hours(rec: Dict[str, Any]) -> Optional[float]:
    """
    Compute video age (in hours) using:

        age = latest_stats_ts - publishedAt

    Priority:
      1) stats.latest_stats_ts (if present)
      2) root.latest_stats_ts (set by track_once)
      3) Last timestamp from stats_snapshots

    Returns:
      - float hours if timestamps are available
      - None if we cannot derive any valid pair of timestamps
    """
    # publishedAt
    sn = _loads_fast(rec.get("snippet")) or {}
    pub = sn.get("publishedAt") or rec.get("publishedAt")
    pub_dt = to_datetime(pub, utc=True, errors="coerce")
    if pub_dt is None or str(pub_dt) == "NaT":
        return None

    # Prefer stats.latest_stats_ts if present; otherwise use root.latest_stats_ts
    stats = rec.get("stats") or {}
    latest_ts = stats.get("latest_stats_ts") or rec.get("latest_stats_ts")

    latest_dt = to_datetime(latest_ts, utc=True, errors="coerce") if latest_ts else None

    # Fallback: use last snapshot timestamp
    if latest_dt is None or str(latest_dt) == "NaT":
        snaps = _loads_fast(rec.get("stats_snapshots")) or _loads_fast(
            rec.get("stats.snapshots")
        )
        if isinstance(snaps, list) and snaps:
            last_ts_val: Optional[str] = None
            for s in snaps:
                s = _loads_fast(s) if isinstance(s, str) else s
                if not isinstance(s, dict):
                    continue
                ts = s.get("ts") or s.get("timestamp") or s.get("time")
                if ts:
                    last_ts_val = ts
            if last_ts_val:
                latest_dt = to_datetime(last_ts_val, utc=True, errors="coerce")

    if latest_dt is None or str(latest_dt) == "NaT":
        return None

    age_hours = (latest_dt - pub_dt).total_seconds() / 3600.0
    return float(age_hours)


# ============================================================
# Feature sets (aligned with training logic for 6h / 12h / 24h)
# ============================================================

HARDER_FEATURES_6H = [
    # Early views (up to 1h)
    "views_10m",
    "views_30m",
    "views_1h",
    # Early likes & comments (up to 1h)
    "likes_10m",
    "likes_30m",
    "likes_1h",
    "comms_10m",
    "comms_30m",
    "comms_1h",
    # Very early growth / slope
    "g_10m_to_1h",
    "slope_views_10m_to_1h",
    # Some 3h engagement information
    "like_rate_3h",
    "comm_rate_3h",
    # Meta
    "durationSec",
]

HARDER_FEATURES_12H = [
    # Views up to 3h
    "views_10m",
    "views_30m",
    "views_1h",
    "views_3h",
    # Likes / comments up to 3h
    "likes_10m",
    "likes_30m",
    "likes_1h",
    "likes_3h",
    "comms_10m",
    "comms_30m",
    "comms_1h",
    "comms_3h",
    # Growth & slopes up to 3h
    "g_10m_to_1h",
    "g_1h_to_3h",
    "slope_views_10m_to_1h",
    "slope_views_1h_to_3h",
    # 3h engagement
    "like_rate_3h",
    "comm_rate_3h",
    # Meta
    "durationSec",
]

# Base 24h feature list used when model does not expose feature_names_in_
FEATURES_24H_HARD_BASE = [
    # 0–24h raw stats
    "views_10m",
    "views_30m",
    "views_1h",
    "views_3h",
    "views_6h",
    "views_12h",
    "views_24h",
    "likes_10m",
    "likes_30m",
    "likes_1h",
    "likes_3h",
    "likes_6h",
    "likes_12h",
    "likes_24h",
    "comms_10m",
    "comms_30m",
    "comms_1h",
    "comms_3h",
    "comms_6h",
    "comms_12h",
    "comms_24h",
    "g_10m_to_1h",
    "g_1h_to_3h",
    "g_3h_to_6h",
    "g_6h_to_12h",
    "g_1h_to_6h",
    "g_6h_to_24h",
    "g_1h_to_24h",
    "g_12h_to_24h",
    "slope_views_10m_to_1h",
    "slope_views_1h_to_3h",
    "slope_views_3h_to_6h",
    "slope_views_6h_to_12h",
    "slope_views_12h_to_24h",
    "slope_views_1h_to_6h",
    "slope_views_6h_to_24h",
    "like_rate_3h",
    "like_rate_6h",
    "like_rate_12h",
    "like_rate_24h",
    "comm_rate_3h",
    "comm_rate_6h",
    "comm_rate_12h",
    "comm_rate_24h",
    # Meta & extra
    "durationSec",
    "isShorts",
    "title_len",
    "desc_len",
    "hashtag_count",
    "upload_hour",
    "upload_dow",
    "lowq_3h_score",
    "lowq_6h_score",
    "lowq_ever_flagged",
    # Note: len_* one-hot features (if used) come from the model's feature list
]

# Global 24h feature metadata (populated from the model)
FEATURES_24H_FULL: List[str] = []
LEN_COLS_24H: List[str] = []
FEATURES_24H_BASE_ONLY: List[str] = []


def init_24h_features_from_model(model) -> List[str]:
    """
    Initialize 24h feature metadata from the model:

      - FEATURES_24H_FULL: complete list of features for the 24h model
      - LEN_COLS_24H: any lengthBucket one-hot columns (prefix "len_")
      - FEATURES_24H_BASE_ONLY: full list minus len_* columns

    Priority for feature list:
      1) model.feature_list_24h (embedded at training time)
      2) model.feature_names_in_ (scikit-learn-style metadata)
      3) fallback: FEATURES_24H_HARD_BASE
    """
    global FEATURES_24H_FULL, LEN_COLS_24H, FEATURES_24H_BASE_ONLY

    feats = getattr(model, "feature_list_24h", None)
    if feats is None:
        feats = getattr(model, "feature_names_in_", None)

    if feats is not None:
        FEATURES_24H_FULL = [str(f) for f in feats]
        print(f"[24H] Using {len(FEATURES_24H_FULL)} features from model metadata")
    else:
        FEATURES_24H_FULL = FEATURES_24H_HARD_BASE
        print(
            "[24H] WARNING: model has no embedded feature list; "
            "falling back to FEATURES_24H_HARD_BASE (no len_* one-hot)."
        )

    LEN_COLS_24H = [c for c in FEATURES_24H_FULL if c.startswith("len_")]
    FEATURES_24H_BASE_ONLY = [c for c in FEATURES_24H_FULL if not c.startswith("len_")]

    return FEATURES_24H_FULL


# ============================================================
# Meta-feature helpers for 24h
# ============================================================

def build_meta_features_for_24h(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Construct meta features for the 24h model:

      - isShorts        : 1 if lengthBucket == "short" (including snippet/agg)
      - title_len       : character count of title
      - desc_len        : character count of description
      - hashtag_count   : frequency of '#' in title+description
      - upload_hour     : hour-of-day (0–23) in UTC
      - upload_dow      : day-of-week (0=Monday, 6=Sunday)
    """
    sn = _loads_fast(rec.get("snippet")) or {}
    title = (sn.get("title") or "").strip()
    desc = (sn.get("description") or "").strip()

    is_shorts = 1 if (sn.get("lengthBucket") == "short" or rec.get("lengthBucket") == "short") else 0  # type: ignore[index]  # noqa: E501

    text = f"{title}\n{desc}"
    hashtag_count = text.count("#")

    pub = sn.get("publishedAt") or rec.get("publishedAt")
    dt = to_datetime(pub, utc=True, errors="coerce")
    if dt is None or str(dt) == "NaT":
        upload_hour = np.nan
        upload_dow = np.nan
    else:
        upload_hour = int(dt.hour)
        upload_dow = int(dt.weekday())

    return {
        "isShorts": float(is_shorts),
        "title_len": float(len(title)),
        "desc_len": float(len(desc)),
        "hashtag_count": float(hashtag_count),
        "upload_hour": float(upload_hour),
        "upload_dow": float(upload_dow),
    }


def build_lowq_features(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Use low-quality flags to construct additional features for the 24h model:

      - lowq_3h_score       : ml_flags.low_quality_v1_3h.score (or 0.0)
      - lowq_6h_score       : ml_flags.low_quality_v3_6h.score (or 0.0)
      - lowq_ever_flagged   : 1 if any low-quality model marked is_low=True
    """
    ml = rec.get("ml_flags") or {}
    lq3 = ml.get("low_quality_v1_3h", {}) or {}
    lq6 = ml.get("low_quality_v3_6h", {}) or {}

    score3 = float(lq3.get("score") or 0.0)
    score6 = float(lq6.get("score") or 0.0)
    ever_flagged = 1.0 if (lq3.get("is_low") or lq6.get("is_low")) else 0.0

    return {
        "lowq_3h_score": score3,
        "lowq_6h_score": score6,
        "lowq_ever_flagged": ever_flagged,
    }


def make_len_dummies(length_bucket: Optional[str]) -> Dict[str, float]:
    """
    Build one-hot encoding for lengthBucket according to LEN_COLS_24H.

    Behavior:
      - If LEN_COLS_24H is empty (model doesn't use len_*), returns {}.
      - If length_bucket is None → try len_nan if present.
      - If length_bucket not present in LEN_COLS_24H → fall back to len_other
        or len_nan if available.
    """
    if not LEN_COLS_24H:
        return {}

    out = {c: 0.0 for c in LEN_COLS_24H}
    if length_bucket is None:
        key = "len_nan"
    else:
        key = f"len_{length_bucket}"

    if key not in out:
        # Fallback: len_other or len_nan if they exist
        if "len_other" in out:
            key = "len_other"
        elif "len_nan" in out:
            key = "len_nan"
        else:
            return out

    out[key] = 1.0
    return out


# ============================================================
# Build feature vectors for each stage
# ============================================================

def build_features_6h(agg: Dict[str, Any]) -> Dict[str, float]:
    """
    Build feature dict for the 6h model based on HARDER_FEATURES_6H.

    `agg` here is the 0–24h aggregation; we only pick the subset needed.
    """
    row: Dict[str, float] = {}
    for col in HARDER_FEATURES_6H:
        row[col] = float(agg.get(col, 0.0))
    return row


def build_features_12h(agg: Dict[str, Any]) -> Dict[str, float]:
    """
    Build feature dict for the 12h model based on HARDER_FEATURES_12H.
    """
    row: Dict[str, float] = {}
    for col in HARDER_FEATURES_12H:
        row[col] = float(agg.get(col, 0.0))
    return row


def build_features_24h(rec: Dict[str, Any], agg: Dict[str, Any]) -> Dict[str, float]:
    """
    Build the full 24h feature vector according to FEATURES_24H_FULL.

    Combines:
      - 0–24h aggregation (`agg`)
      - meta features (isShorts, title_len, ...)
      - low-quality features (lowq_*)
      - len_* one-hot encodings (if present in FEATURES_24H_FULL)
    """
    meta = build_meta_features_for_24h(rec)
    lowq = build_lowq_features(rec)
    len_dummy = make_len_dummies(agg.get("lengthBucket"))

    row: Dict[str, float] = {}

    for col in FEATURES_24H_BASE_ONLY:
        if col in agg:
            row[col] = float(agg.get(col, 0.0))
        elif col in meta:
            row[col] = float(meta.get(col, 0.0))
        elif col in lowq:
            row[col] = float(lowq.get(col, 0.0))
        else:
            # If the column is not present anywhere (missing / NaN), default to 0.
            row[col] = 0.0

    # Add len_* one-hot columns, if any
    for col, val in len_dummy.items():
        row[col] = float(val)

    # Ensure all columns from FEATURES_24H_FULL are present
    for col in FEATURES_24H_FULL:
        row.setdefault(col, 0.0)

    return row


# ============================================================
# Mongo helpers
# ============================================================

def get_collection(mongo_uri: str, db_name: str, coll_name: str):
    """Connect to MongoDB and return the target collection handle."""
    client = MongoClient(mongo_uri)
    db = client[db_name]
    return db[coll_name]


def fetch_candidates(
    coll,
    stage: str,
    only_missing: bool,
    limit: Optional[int] = None,
):
    """
    Fetch candidate videos to score for a given stage.

    Args:
        stage:
            - "h6"
            - "h12"
            - "h24_validation"
        only_missing:
            - If True, only return videos whose
              ml_flags.viral_v2.<stage>.score_proba is None/missing.
            - If False, no filter is applied on score_proba.
        limit:
            - Optional max number of docs (for debugging / safety).

    NOTE:
        We do NOT filter by tracking.status here. The idea is to allow
        recomputation regardless of tracking status, as long as the video
        has stats snapshots and passes the age gating inside run_stage().
    """
    field_score = f"ml_flags.viral_v2.{stage}.score_proba"

    query: Dict[str, Any] = {
        "stats_snapshots.0": {"$exists": True},
    }
    if only_missing:
        # In Mongo, field == None matches both missing field and explicit null.
        query[field_score] = None

    projection = {
        "_id": 1,
        "snippet": 1,
        "stats_snapshots": 1,
        "stats": 1,
        "durationSec": 1,
        "lengthBucket": 1,
        "ml_flags": 1,
        "tracking": 1,
        "source": 1,
        "publishedAt": 1,
        "latest_stats_ts": 1,
    }

    cursor = coll.find(query, projection=projection)
    if limit is not None and limit > 0:
        cursor = cursor.limit(limit)
    return cursor


def ensure_viral_flags(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure rec["ml_flags"]["viral_v2"] exists with a default structure.

    This function mutates the in-memory Python dict only.
    Actual persistence to MongoDB is done using `$set` in run_stage().
    """
    ml = rec.setdefault("ml_flags", {})
    viral = ml.setdefault("viral_v2", {})
    viral.setdefault("model_version", MODEL_VERSION)
    viral.setdefault("label_rule_version", LABEL_RULE_VERSION)
    viral.setdefault(
        "h6",
        {
            "score_proba": None,
            "score_100": None,
            "is_candidate": None,
            "threshold_proba": 0.6,
            "threshold_100": 60,
            "evaluated_at": None,
        },
    )
    viral.setdefault(
        "h12",
        {
            "score_proba": None,
            "score_100": None,
            "is_viral_12h": None,
            "threshold_proba": 0.7,
            "threshold_100": 70,
            "evaluated_at": None,
        },
    )
    viral.setdefault(
        "h24_validation",
        {
            "score_proba": None,
            "score_100": None,
            "evaluated_at": None,
        },
    )
    viral.setdefault(
        "final",
        {
            "status": "unknown",
            "decided_stage": None,
            "score_proba": None,
            "score_100": None,
            "threshold_proba": None,
            "threshold_100": None,
            "decided_at": None,
            "reason": None,
        },
    )
    return rec


def utc_now_iso() -> str:
    """Return current UTC time as an ISO-8601 string (timezone-aware)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Core runners for each stage
# ============================================================

def run_stage(
    stage: str,
    model_path: Path,
    feature_cols: List[str],
    build_features_fn,
    mongo_uri: str,
    db_name: str,
    coll_name: str,
    only_missing: bool,
    force_all: bool,
    limit: Optional[int],
    worker_runs_name: Optional[str] = None,
    worker_status: Optional[str] = None,
) -> None:
    """
    Generic runner for a single stage.

    Args:
        stage:
            - "h6"
            - "h12"
            - "h24_validation"
        model_path:
            Path to the .joblib model used for this stage.
        feature_cols:
            Ordered list of feature names to feed into the model
            (ignored for 24h, where we derive from model metadata).
        build_features_fn:
            Callable(rec, agg) -> feature dict.
            For 6h/12h, agg is used; for 24h both rec+agg are used.
        mongo_uri/db_name/coll_name:
            MongoDB connection parameters.
        only_missing:
            If True, only consider videos missing score_proba for this stage.
        force_all:
            If True, ignore only_missing and recompute for all candidates
            (still subject to age gating per stage).
        limit:
            Optional limit on number of videos (debug/safety).
        worker_runs_name/worker_status:
            Identifiers written into worker_runs to support dashboards.
    """
    if not model_path.exists():
        raise SystemExit(f"[ERROR] Model file not found: {model_path}")

    print(
        f"[CFG] Stage={stage}, model={model_path}, "
        f"only_missing={only_missing}, force_all={force_all}, limit={limit}"
    )

    model = joblib.load(model_path)

    # For 24h, feature_cols are dynamically taken from the model metadata
    if stage == "h24_validation":
        feature_cols = init_24h_features_from_model(model)

    coll = get_collection(mongo_uri, db_name, coll_name)

    # If force_all=True, we ignore only_missing but still respect age gating
    cursor = fetch_candidates(
        coll, stage=stage, only_missing=(only_missing and not force_all), limit=limit
    )

    total = 0
    updated = 0

    for rec in cursor:
        total += 1
        vid = rec.get("_id")

        # Compute video age in hours from latest_stats_ts - publishedAt
        age_hrs = compute_video_age_hours(rec)
        if age_hrs is None:
            print(f"[SKIP] _id={vid}: cannot compute age_hrs (missing timestamps)")
            continue

        # Stage-specific age gating:
        #   - h6  : age >= 6h
        #   - h12 : age >= 12h
        #   - h24 : age >= 23h (slightly early to catch almost-24h)
        if stage == "h6" and age_hrs < 6.0:
            continue
        if stage == "h12" and age_hrs < 12.0:
            continue
        if stage == "h24_validation" and age_hrs < 23.0:
            continue

        agg = aggregate_0_24h(rec)
        if agg is None:
            print(f"[SKIP] _id={vid}: aggregation failed")
            continue

        # Build feature row
        feat_dict = build_features_fn(rec, agg)
        # Ensure correct ordering and full coverage for all feature columns
        x = np.array([[feat_dict.get(c, 0.0) for c in feature_cols]], dtype=np.float32)

        proba = float(model.predict_proba(x)[0, 1])
        score_100 = int(round(proba * 100))

        # Ensure ml_flags.viral_v2 exists, then update only the intended paths
        ensure_viral_flags(rec)
        ml_viral = rec["ml_flags"]["viral_v2"]
        now_iso = utc_now_iso()

        set_fields: Dict[str, Any] = {
            "ml_flags.viral_v2.model_version": MODEL_VERSION,
            "ml_flags.viral_v2.label_rule_version": LABEL_RULE_VERSION,
        }

        if stage == "h6":
            h6 = ml_viral.get("h6", {})
            thr_p = float(h6.get("threshold_proba", 0.6))
            thr_100 = int(h6.get("threshold_100", 60))
            is_candidate = bool(proba >= thr_p or score_100 >= thr_100)
            set_fields.update(
                {
                    "ml_flags.viral_v2.h6.score_proba": proba,
                    "ml_flags.viral_v2.h6.score_100": score_100,
                    "ml_flags.viral_v2.h6.is_candidate": is_candidate,
                    "ml_flags.viral_v2.h6.threshold_proba": thr_p,
                    "ml_flags.viral_v2.h6.threshold_100": thr_100,
                    "ml_flags.viral_v2.h6.evaluated_at": now_iso,
                }
            )

        elif stage == "h12":
            h12 = ml_viral.get("h12", {})
            thr_p = float(h12.get("threshold_proba", 0.7))
            thr_100 = int(h12.get("threshold_100", 70))
            is_viral_12h = bool(proba >= thr_p or score_100 >= thr_100)
            set_fields.update(
                {
                    "ml_flags.viral_v2.h12.score_proba": proba,
                    "ml_flags.viral_v2.h12.score_100": score_100,
                    "ml_flags.viral_v2.h12.is_viral_12h": is_viral_12h,
                    "ml_flags.viral_v2.h12.threshold_proba": thr_p,
                    "ml_flags.viral_v2.h12.threshold_100": thr_100,
                    "ml_flags.viral_v2.h12.evaluated_at": now_iso,
                }
            )

        elif stage == "h24_validation":
            set_fields.update(
                {
                    "ml_flags.viral_v2.h24_validation.score_proba": proba,
                    "ml_flags.viral_v2.h24_validation.score_100": score_100,
                    "ml_flags.viral_v2.h24_validation.evaluated_at": now_iso,
                }
            )
        else:
            raise ValueError(f"Unknown stage: {stage}")

        res = coll.update_one({"_id": vid}, {"$set": set_fields})
        if res.modified_count:
            updated += 1

        if total % 50 == 0:
            print(
                f"[{stage}] processed={total:,}, updated={updated:,} "
                f"(last _id={vid}, score={score_100}, age_hrs={age_hrs:.2f})"
            )

    print(
        f"[DONE-{stage}] total scanned={total:,}, docs updated={updated:,}, "
        f"model={model_path}"
    )

    # ---- Record worker run in worker_runs for dashboards / monitoring ----
    from datetime import datetime, timezone

    doc_name = worker_runs_name or f"viral_scoring_{stage}"
    status_val = worker_status or f"ok_{stage}"

    coll.database["worker_runs"].update_one(
        {"name": doc_name},
        {
            "$set": {
                "last_run": datetime.now(timezone.utc),
                "status": status_val,
            }
        },
        upsert=True,
    )


# ============================================================
# CLI
# ============================================================

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """
    CLI parser for running viral models:

      python -m worker.viral_prediction_core 6h  [--only-missing|--force-all ...]
      python -m worker.viral_prediction_core 12h [...]
      python -m worker.viral_prediction_core 24h [...]
    """
    parser = argparse.ArgumentParser(
        description="Run viral prediction models (6h / 12h / 24h) on MongoDB."
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(subparser: argparse.ArgumentParser):
        subparser.add_argument(
            "--mongo-uri",
            default=DEFAULT_MONGO_URI,
            help=f"Mongo URI (default: {DEFAULT_MONGO_URI})",
        )
        subparser.add_argument(
            "--db",
            default=DEFAULT_DB_NAME,
            help=f"DB name (default: {DEFAULT_DB_NAME})",
        )
        subparser.add_argument(
            "--collection",
            default=DEFAULT_COLLECTION,
            help=f"Collection name (default: {DEFAULT_COLLECTION})",
        )
        subparser.add_argument(
            "--only-missing",
            action="store_true",
            help="Only score videos that have no score_proba for this stage yet.",
        )
        subparser.add_argument(
            "--force-all",
            action="store_true",
            help=(
                "Ignore --only-missing and recompute for all videos with enough data; "
                "age gating per stage is still enforced."
            ),
        )
        subparser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit number of videos (debug). Default: no limit.",
        )

    p6 = sub.add_parser("6h", help="Run 6h early-signal model (ml_flags.viral_v2.h6)")
    add_common(p6)

    p12 = sub.add_parser(
        "12h", help="Run 12h confirmation model (ml_flags.viral_v2.h12)"
    )
    add_common(p12)

    p24 = sub.add_parser(
        "24h",
        help="Run 24h validator model (ml_flags.viral_v2.h24_validation)",
    )
    add_common(p24)

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    """
    CLI entry point. Dispatches to run_stage() for the requested command
    (6h, 12h, or 24h).
    """
    args = parse_args(argv)

    if args.cmd == "6h":
        run_stage(
            stage="h6",
            model_path=MODEL_6H_PATH,
            feature_cols=HARDER_FEATURES_6H,
            build_features_fn=lambda rec, agg: build_features_6h(agg),
            mongo_uri=args.mongo_uri,
            db_name=args.db,
            coll_name=args.collection,
            only_missing=args.only_missing,
            force_all=args.force_all,
            limit=args.limit,
            worker_runs_name="viral_prediction_core",
            worker_status="viral_prediction_core",
        )

    elif args.cmd == "12h":
        run_stage(
            stage="h12",
            model_path=MODEL_12H_PATH,
            feature_cols=HARDER_FEATURES_12H,
            build_features_fn=lambda rec, agg: build_features_12h(agg),
            mongo_uri=args.mongo_uri,
            db_name=args.db,
            coll_name=args.collection,
            only_missing=args.only_missing,
            force_all=args.force_all,
            limit=args.limit,
            worker_runs_name="viral_prediction_core",
            worker_status="viral_prediction_core",
        )

    elif args.cmd == "24h":
        # feature_cols will be overridden inside run_stage based on model metadata
        run_stage(
            stage="h24_validation",
            model_path=MODEL_24H_PATH,
            feature_cols=[],
            build_features_fn=build_features_24h,
            mongo_uri=args.mongo_uri,
            db_name=args.db,
            coll_name=args.collection,
            only_missing=args.only_missing,
            force_all=args.force_all,
            limit=args.limit,
            worker_runs_name="viral_prediction_core",
            worker_status="viral_prediction_core",
        )

    else:  # pragma: no cover
        raise SystemExit(f"Unknown command: {args.cmd}")


if __name__ == "__main__":  # pragma: no cover
    main()
