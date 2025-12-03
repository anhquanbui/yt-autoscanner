# -*- coding: utf-8 -*-
"""
worker.viral_prediction_core (multiclass v2)

Core logic for running the three viral models (6h, 12h, 24h) directly on MongoDB
and writing results into `ml_flags.viral_v2.*`.

New in this version
-------------------
- Uses MULTICLASS XGBoost models with 4 classes:
    0 = non_viral
    1 = weak_viral
    2 = viral
    3 = super_viral
- For each stage we store:
    * per-class probabilities
    * aggregated "any viral" probability (weak+viral+super)
    * top_class
    * score_100 = round(100 * P(any viral))
- Keeps the old fields so that `viral_finalize.py` and dashboards
  do not break (score_proba, score_100, is_candidate / is_viral_12h, ...).

Stages:
    - 6h  : early signal          → ml_flags.viral_v2.h6
    - 12h : confirmation          → ml_flags.viral_v2.h12
    - 24h : late validator        → ml_flags.viral_v2.h24

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
import sys
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from pandas import to_datetime
from pymongo import MongoClient
import joblib

from config.env import load_env, get_env  # type: ignore

# Load environment variables once (shared logic with other workers)
load_env()

try:
    import orjson as _orjson
except Exception:  # pragma: no cover - optional dependency
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

# Defaults point to the new *multiclass* models. You can still override
# them via VIRAL_MODEL_6H / 12H / 24H if you keep older filenames.
MODEL_6H_PATH = Path(
    os.getenv("VIRAL_MODEL_6H", str(MODEL_DIR / "viral_xgb_6h.joblib"))
)
MODEL_12H_PATH = Path(
    os.getenv("VIRAL_MODEL_12H", str(MODEL_DIR / "viral_xgb_12h.joblib"))
)
MODEL_24H_PATH = Path(
    os.getenv("VIRAL_MODEL_24H", str(MODEL_DIR / "viral_xgb_24h.joblib"))
)

# Version metadata written into ml_flags.viral_v2
MODEL_VERSION = int(os.getenv("VIRAL_MODEL_VERSION", "2"))
LABEL_RULE_VERSION = int(os.getenv("VIRAL_LABEL_RULE_VERSION", "2"))

# Multiclass label names (index -> name)
MULTI_CLASS_LABELS = ["non_viral", "weak_viral", "viral", "super_viral"]

# 24h feature metadata (populated lazily from the 24h model)
FEATURES_24H_HARD_BASE: List[str] = [
    # Raw counts up to 20h
    "views_10m", "views_30m", "views_1h", "views_3h", "views_6h",
    "views_12h", "views_20h",
    "likes_10m", "likes_30m", "likes_1h", "likes_3h", "likes_6h",
    "likes_12h", "likes_20h",
    "comms_10m", "comms_30m", "comms_1h", "comms_3h", "comms_6h",
    "comms_12h", "comms_20h",
    # Growth up to 12h
    "g_10m_to_1h", "g_1h_to_3h", "g_3h_to_6h", "g_6h_to_12h",
    # Extended growth towards 20h
    "g_6h_to_20h", "g_12h_to_20h",
    # Engagement quality up to 12h
    "like_rate_3h", "like_rate_6h", "like_rate_12h",
    "comm_rate_3h", "comm_rate_6h", "comm_rate_12h",
]

FEATURES_24H_FULL: List[str] = []          # final full list (including len_* one-hot)
FEATURES_24H_BASE_ONLY: List[str] = []     # non-len_* subset
LEN_COLS_24H: List[str] = []               # len_* columns

# Feature lists for 6h / 12h — these MUST match the training notebook
HARDER_FEATURES_6H: List[str] = [
    # Raw counts up to 3h
    "views_10m", "views_30m", "views_1h", "views_3h",
    "likes_10m", "likes_30m", "likes_1h", "likes_3h",
    "comms_10m", "comms_30m", "comms_1h", "comms_3h",
    # Growth & slopes in early window
    "g_10m_to_1h", "g_1h_to_3h",
    "slope_views_10m_to_1h", "slope_views_1h_to_3h",
    # Early engagement quality
    "like_rate_1h", "like_rate_3h",
    "comm_rate_1h", "comm_rate_3h",
    # Shape of early spike
    "ratio_views_30m_10m", "ratio_views_1h_30m",
    "ratio_likes_30m_10m", "ratio_likes_1h_30m",
    "ratio_comms_30m_10m", "ratio_comms_1h_30m",
]

HARDER_FEATURES_12H: List[str] = [
    # Raw counts up to 6h
    "views_10m", "views_30m", "views_1h", "views_3h", "views_6h",
    "likes_10m", "likes_30m", "likes_1h", "likes_3h", "likes_6h",
    "comms_10m", "comms_30m", "comms_1h", "comms_3h", "comms_6h",
    # Growth across 0–1–3–6h
    "g_10m_to_1h", "g_1h_to_3h", "g_3h_to_6h",
    # Slopes
    "slope_views_10m_to_1h",
    "slope_views_1h_to_3h",
    "slope_views_3h_to_6h",
    # Engagement quality
    "like_rate_1h", "like_rate_3h", "like_rate_6h",
    "comm_rate_1h", "comm_rate_3h", "comm_rate_6h",
    # Early spike shape
    "ratio_views_30m_10m", "ratio_views_1h_30m",
    "ratio_likes_30m_10m", "ratio_likes_1h_30m",
    "ratio_comms_30m_10m", "ratio_comms_1h_30m",
]


# ============================================================
# JSON helpers
# ============================================================

def _loads_fast(x: Any) -> Any:
    if x is None:
        return None
    if isinstance(x, (dict, list)):
        return x
    if isinstance(x, str):
        try:
            if _orjson is not None:
                return _orjson.loads(x)
            import json
            return json.loads(x)
        except Exception:
            return None
    return None


# ============================================================
# Small numeric helpers
# ============================================================

def _safe_int(x: Any) -> int:
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return 0


def _safe_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return float("nan")


# ============================================================
# 0–24h aggregation from stats_snapshots
# ============================================================

def aggregate_0_24h(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Aggregate raw stats snapshots of a video into fixed buckets between 0–24h.

    Returns a dict that contains:
      - views_*, likes_*, comms_* at 10m/30m/1h/3h/6h/12h/20h/24h
      - growth ratios g_*
      - slopes for views over several windows
      - engagement rates like_rate_*, comm_rate_*
      - durationSec, lengthBucket

    If required information is missing, returns None and the caller should skip.
    """
    vid = rec.get("videoId") or rec.get("id") or rec.get("_id")

    snippet = rec.get("snippet") or {}
    if isinstance(snippet, str):
        snippet = _loads_fast(snippet) or {}

    published_at = snippet.get("publishedAt") or rec.get("publishedAt")
    t0 = to_datetime(published_at, utc=True, errors="coerce")
    if t0 is None or str(t0) == "NaT":
        return None

    # Stats snapshots can live in several schema variants, be generous:
    snaps = (
        rec.get("stats_snapshots")
        or (rec.get("stats") or {}).get("snapshots")
        or []
    )
    if isinstance(snaps, str):
        snaps = _loads_fast(snaps) or []
    if isinstance(snaps, dict):
        snaps = snaps.get("items") or snaps.get("data") or []
    if not isinstance(snaps, list):
        return None

    th: List[float] = []
    v: List[int] = []
    l: List[int] = []
    c: List[int] = []

    for s in snaps:
        if isinstance(s, str):
            s = _loads_fast(s) or {}
        if not isinstance(s, dict):
            continue

        ts = s.get("ts") or s.get("timestamp") or s.get("time")
        if not ts:
            continue
        t = to_datetime(ts, utc=True, errors="coerce")
        if t is None or str(t) == "NaT":
            continue

        h = (t - t0).total_seconds() / 3600.0
        if h < -0.1:  # ignore clearly invalid negatives
            continue

        th.append(h)
        v.append(_safe_int(s.get("viewCount")))
        l.append(_safe_int(s.get("likeCount")))
        c.append(_safe_int(s.get("commentCount")))

    if not th:
        return None

    # Sort just in case
    arr = sorted(zip(th, v, l, c), key=lambda z: z[0])
    th = [a[0] for a in arr]
    v = [a[1] for a in arr]
    l = [a[2] for a in arr]
    c = [a[3] for a in arr]

    def interp_at(hours: float, series: List[int]) -> float:
        if not series or not th:
            return float("nan")
        return float(np.interp(hours, th, series, left=np.nan, right=np.nan))

    # Views at different horizons
    v10 = interp_at(1.0 / 6.0, v)
    v30 = interp_at(0.5, v)
    v1 = interp_at(1.0, v)
    v3 = interp_at(3.0, v)
    v6 = interp_at(6.0, v)
    v12 = interp_at(12.0, v)
    v20 = interp_at(20.0, v)
    v24 = interp_at(24.0, v)

    # Likes
    l10 = interp_at(1.0 / 6.0, l)
    l30 = interp_at(0.5, l)
    l1 = interp_at(1.0, l)
    l3 = interp_at(3.0, l)
    l6 = interp_at(6.0, l)
    l12 = interp_at(12.0, l)
    l20 = interp_at(20.0, l)
    l24 = interp_at(24.0, l)

    # Comments
    c10 = interp_at(1.0 / 6.0, c)
    c30 = interp_at(0.5, c)
    c1 = interp_at(1.0, c)
    c3 = interp_at(3.0, c)
    c6 = interp_at(6.0, c)
    c12 = interp_at(12.0, c)
    c20 = interp_at(20.0, c)
    c24 = interp_at(24.0, c)

    def growth(a: float, b: float) -> float:
        if not np.isfinite(a) or not np.isfinite(b) or a <= 0:
            return float("nan")
        return (b - a) / max(a, 1.0)

    def rate(num: float, den: float) -> float:
        if not np.isfinite(den) or den <= 0:
            return float("nan")
        return num / den

    # Growth ratios
    g_10_1 = growth(v10, v1)
    g_1_3 = growth(v1, v3)
    g_3_6 = growth(v3, v6)
    g_6_12 = growth(v6, v12)
    g_6_20 = growth(v6, v20)
    g_12_20 = growth(v12, v20)
    g_6_24 = growth(v6, v24)
    g_1_24 = growth(v1, v24)

    # Slopes in views
    eps = 1e-6
    slope_10_1 = (v1 - v10) / max(0.8333, eps)   # ~50 minutes
    slope_1_3 = (v3 - v1) / max(2.0, eps)
    slope_3_6 = (v6 - v3) / max(3.0, eps)
    slope_6_12 = (v12 - v6) / max(6.0, eps)
    slope_12_24 = (v24 - v12) / max(12.0, eps)
    slope_1_6 = (v6 - v1) / max(5.0, eps)
    slope_6_24 = (v24 - v6) / max(18.0, eps)
    slope_1_24 = (v24 - v1) / max(23.0, eps)

    # Ratios that describe shape of spike
    def ratio(a: float, b: float) -> float:
        if not np.isfinite(b) or b <= 0:
            return float("nan")
        return a / max(b, 1.0)

    ratio_views_30_10 = ratio(v30, v10)
    ratio_views_1_30 = ratio(v1, v30)
    ratio_likes_30_10 = ratio(l30, l10)
    ratio_likes_1_30 = ratio(l1, l30)
    ratio_comms_30_10 = ratio(c30, c10)
    ratio_comms_1_30 = ratio(c1, c30)

    # Engagement quality
    like_rate_1h = rate(l1, v1)
    like_rate_3h = rate(l3, v3)
    like_rate_6h = rate(l6, v6)
    like_rate_12h = rate(l12, v12)
    like_rate_24h = rate(l24, v24)

    comm_rate_1h = rate(c1, v1)
    comm_rate_3h = rate(c3, v3)
    comm_rate_6h = rate(c6, v6)
    comm_rate_12h = rate(c12, v12)
    comm_rate_24h = rate(c24, v24)

    duration = _safe_float(snippet.get("durationSec") or rec.get("durationSec"))
    length_bucket = rec.get("lengthBucket") or snippet.get("lengthBucket")

    return {
        "videoId": vid,
        "publishedAt": published_at,
        # raw views
        "views_10m": v10, "views_30m": v30, "views_1h": v1,
        "views_3h": v3, "views_6h": v6, "views_12h": v12,
        "views_20h": v20, "views_24h": v24,
        # raw likes
        "likes_10m": l10, "likes_30m": l30, "likes_1h": l1,
        "likes_3h": l3, "likes_6h": l6, "likes_12h": l12,
        "likes_20h": l20, "likes_24h": l24,
        # raw comments
        "comms_10m": c10, "comms_30m": c30, "comms_1h": c1,
        "comms_3h": c3, "comms_6h": c6, "comms_12h": c12,
        "comms_20h": c20, "comms_24h": c24,
        # growth
        "g_10m_to_1h": g_10_1,
        "g_1h_to_3h": g_1_3,
        "g_3h_to_6h": g_3_6,
        "g_6h_to_12h": g_6_12,
        "g_6h_to_20h": g_6_20,
        "g_12h_to_20h": g_12_20,
        "g_6h_to_24h": g_6_24,
        "g_1h_to_24h": g_1_24,
        # slopes
        "slope_views_10m_to_1h": slope_10_1,
        "slope_views_1h_to_3h": slope_1_3,
        "slope_views_3h_to_6h": slope_3_6,
        "slope_views_6h_to_12h": slope_6_12,
        "slope_views_12h_to_24h": slope_12_24,
        "slope_views_1h_to_6h": slope_1_6,
        "slope_views_6h_to_24h": slope_6_24,
        "slope_views_1h_to_24h": slope_1_24,
        # ratios
        "ratio_views_30m_10m": ratio_views_30_10,
        "ratio_views_1h_30m": ratio_views_1_30,
        "ratio_likes_30m_10m": ratio_likes_30_10,
        "ratio_likes_1h_30m": ratio_likes_1_30,
        "ratio_comms_30m_10m": ratio_comms_30_10,
        "ratio_comms_1h_30m": ratio_comms_1_30,
        # engagement
        "like_rate_1h": like_rate_1h,
        "like_rate_3h": like_rate_3h,
        "like_rate_6h": like_rate_6h,
        "like_rate_12h": like_rate_12h,
        "like_rate_24h": like_rate_24h,
        "comm_rate_1h": comm_rate_1h,
        "comm_rate_3h": comm_rate_3h,
        "comm_rate_6h": comm_rate_6h,
        "comm_rate_12h": comm_rate_12h,
        "comm_rate_24h": comm_rate_24h,
        # misc
        "durationSec": duration,
        "lengthBucket": length_bucket,
    }


# ============================================================
# 24h meta-features
# ============================================================

def ensure_24h_feature_list(model: Any) -> List[str]:
    """
    Decide which columns to use for the 24h model.

    Priority:
      1) model.feature_list_24h        (embedded manually at training time)
      2) model.feature_names_in_       (scikit-learn metadata)
      3) fallback: FEATURES_24H_HARD_BASE
    """
    global FEATURES_24H_FULL, FEATURES_24H_BASE_ONLY, LEN_COLS_24H

    feats = getattr(model, "feature_list_24h", None)
    if feats is None:
        feats = getattr(model, "feature_names_in_", None)

    if feats is not None:
        FEATURES_24H_FULL = [str(f) for f in feats]
        print(f"[24h] Using {len(FEATURES_24H_FULL)} features from model metadata")
    else:
        FEATURES_24H_FULL = FEATURES_24H_HARD_BASE
        print(
            "[24h] WARNING: model has no embedded feature list; "
            "falling back to FEATURES_24H_HARD_BASE (no len_* one-hot)."
        )

    FEATURES_24H_BASE_ONLY = [c for c in FEATURES_24H_FULL if not c.startswith("len_")]
    LEN_COLS_24H = [c for c in FEATURES_24H_FULL if c.startswith("len_")]
    return FEATURES_24H_FULL


def build_meta_features(rec: Dict[str, Any]) -> Dict[str, float]:
    snippet = rec.get("snippet") or {}
    if isinstance(snippet, str):
        snippet = _loads_fast(snippet) or {}

    title = snippet.get("title") or ""
    desc = snippet.get("description") or ""

    # shorts flag: several schema variants possible
    is_shorts = (
        bool(rec.get("isShorts"))
        or bool(snippet.get("isShorts"))
        or bool(snippet.get("isShort"))
    )

    # hashtags: naive count of words starting with '#'
    hashtag_count = 0
    for text in (title, desc):
        for token in str(text).split():
            if token.startswith("#"):
                hashtag_count += 1

    # upload time meta
    published_at = snippet.get("publishedAt") or rec.get("publishedAt")
    dt = to_datetime(published_at, utc=True, errors="coerce")
    if dt is None or str(dt) == "NaT":
        upload_hour = float("nan")
        upload_dow = float("nan")
    else:
        upload_hour = float(dt.hour)
        upload_dow = float(dt.weekday())

    return {
        "isShorts": float(is_shorts),
        "title_len": float(len(title)),
        "desc_len": float(len(desc)),
        "hashtag_count": float(hashtag_count),
        "upload_hour": upload_hour,
        "upload_dow": upload_dow,
    }


def build_lowq_features(rec: Dict[str, Any]) -> Dict[str, float]:
    """
    Use low-quality flags to construct additional features for the 24h model:

      - lowq_3h_score       : ml_flags.low_quality_v1_3h.score (or 0.0)
      - lowq_6h_score       : ml_flags.low_quality_v3_6h.score (or 0.0)
      - lowq_ever_flagged   : 1 if any low-quality model marked is_low=True
    """
    ml = rec.get("ml_flags") or {}
    lq3 = ml.get("low_quality_v1_3h", {}) or {}
    lq6 = ml.get("low_quality_v3_6h", {}) or {}

    score3 = float(lq3.get("score", 0.0) or 0.0)
    score6 = float(lq6.get("score", 0.0) or 0.0)
    ever_flagged = bool(
        lq3.get("is_low") is True
        or lq6.get("is_low") is True
    )

    return {
        "lowq_3h_score": score3,
        "lowq_6h_score": score6,
        "lowq_ever_flagged": 1.0 if ever_flagged else 0.0,
    }


def one_hot_length_bucket(length_bucket: Any) -> Dict[str, float]:
    """
    Turn lengthBucket into one-hot columns len_short / len_medium / len_long / len_other.
    If the current model's FEATURES_24H_FULL does not contain any len_* columns,
    this will simply return {}.
    """
    if not LEN_COLS_24H:
        return {}

    key_map = {
        "SHORT": "len_short",
        "MEDIUM": "len_medium",
        "LONG": "len_long",
    }

    out = {c: 0.0 for c in LEN_COLS_24H}
    val = str(length_bucket or "").upper()
    key = key_map.get(val)

    if key not in out:
        # Fallback to len_other / len_nan if present
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
    row: Dict[str, float] = {}
    for col in HARDER_FEATURES_6H:
        row[col] = float(agg.get(col, 0.0))
    return row


def build_features_12h(agg: Dict[str, Any]) -> Dict[str, float]:
    row: Dict[str, float] = {}
    for col in HARDER_FEATURES_12H:
        row[col] = float(agg.get(col, 0.0))
    return row


def build_features_24h(rec: Dict[str, Any], agg: Dict[str, Any]) -> Dict[str, float]:
    """
    Build full 24h feature vector:

      - aggregation (agg)
      - meta features
      - low-quality features
      - len_* one-hot (if present in FEATURES_24H_FULL)
    """
    meta = build_meta_features(rec)
    lowq = build_lowq_features(rec)
    len_dummy = one_hot_length_bucket(agg.get("lengthBucket"))

    row: Dict[str, float] = {}

    for col in FEATURES_24H_BASE_ONLY:
        if col in agg:
            row[col] = float(agg.get(col, 0.0))
        elif col in meta:
            row[col] = float(meta.get(col, 0.0))
        elif col in lowq:
            row[col] = float(lowq.get(col, 0.0))
        else:
            row[col] = 0.0

    for col, val in len_dummy.items():
        if col in FEATURES_24H_FULL:
            row[col] = float(val)

    return row


# ============================================================
# Age helper
# ============================================================

def compute_age_hours(rec: Dict[str, Any]) -> float:
    snippet = rec.get("snippet") or {}
    if isinstance(snippet, str):
        snippet = _loads_fast(snippet) or {}

    pub = snippet.get("publishedAt") or rec.get("publishedAt")
    latest_dt = None

    if pub:
        pub_dt = to_datetime(pub, utc=True, errors="coerce")
    else:
        return float("nan")

    # Last snapshot ts
    snaps = rec.get("stats_snapshots") or (rec.get("stats") or {}).get("snapshots")
    if isinstance(snaps, str):
        snaps = _loads_fast(snaps) or []
    if isinstance(snaps, dict):
        snaps = snaps.get("items") or snaps.get("data") or []
    if isinstance(snaps, list) and snaps:
        last = snaps[-1]
        if isinstance(last, str):
            last = _loads_fast(last) or {}
        last_ts = last.get("ts") or last.get("timestamp") or last.get("time")
        latest_dt = to_datetime(last_ts, utc=True, errors="coerce")
    else:
        latest_dt = None

    if latest_dt is None or str(latest_dt) == "NaT":
        return float("nan")

    return float((latest_dt - pub_dt).total_seconds() / 3600.0)


# ============================================================
# Mongo helpers
# ============================================================

def build_cursor(
    coll,
    stage_key: str,
    only_missing: bool,
    limit: Optional[int],
) -> Any:
    """
    Common Mongo cursor for all stages.

    We intentionally restrict scoring to videos that are still actively
    being tracked to avoid wasting work on videos that have already been
    stopped or removed.

    - tracking.status must be "tracking"
    - tracking.stop_reason must be null / missing
    """
    field_score = f"ml_flags.viral_v2.{stage_key}.score_proba"

    query: Dict[str, Any] = {
        "stats_snapshots.0": {"$exists": True},
        "tracking.status": "tracking",
        "tracking.stop_reason": None,
    }
    if only_missing:
        query[field_score] = None

    projection = {
        "_id": 1,
        "snippet": 1,
        "stats_snapshots": 1,
        "stats": 1,
        "ml_flags": 1,
        "lengthBucket": 1,
        "durationSec": 1,
        "publishedAt": 1,
        "tracking": 1,
    }

    cursor = coll.find(query, projection=projection)
    if limit is not None and limit > 0:
        cursor = cursor.limit(limit)
    return cursor


def ensure_viral_flags(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure rec["ml_flags"]["viral_v2"] exists with default structures.
    """
    ml = rec.setdefault("ml_flags", {})
    viral = ml.setdefault("viral_v2", {})

    viral.setdefault("model_version", MODEL_VERSION)
    viral.setdefault("label_rule_version", LABEL_RULE_VERSION)

    # Meta block: store global thresholds / versions for debugging & training
    viral.setdefault(
        "meta",
        {
            "model_version": MODEL_VERSION,
            "label_rule_version": LABEL_RULE_VERSION,
            "thresholds": {
                "h6": {"proba": 0.6, "score_100": 60},
                "h12": {"proba": 0.7, "score_100": 70},
                "h24": {"proba": 0.8, "score_100": 80},
            },
        },
    )

    viral.setdefault(
        "h6",
        {
            "score_proba": None,
            "score_100": None,
            "threshold_proba": 0.6,
            "evaluated_at": None,
            # multiclass extras
            "top_class": None,
            "proba_non": None,
            "proba_weak": None,
            "proba_viral": None,
            "proba_super": None,
        },
    )
    viral.setdefault(
        "h12",
        {
            "score_proba": None,
            "score_100": None,
            "threshold_proba": 0.7,
            "evaluated_at": None,
            # multiclass extras
            "top_class": None,
            "proba_non": None,
            "proba_weak": None,
            "proba_viral": None,
            "proba_super": None,
        },
    )
    viral.setdefault(
        "h24",
        {
            "score_proba": None,
            "score_100": None,
            "threshold_proba": 0.8,
            "evaluated_at": None,
            # multiclass extras
            "top_class": None,
            "proba_non": None,
            "proba_weak": None,
            "proba_viral": None,
            "proba_super": None,
        },
    )
    viral.setdefault(
        "final",
        {
            "status": None,
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
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def upsert_worker_run(
    db,
    name: str,
    stage: str,
    status: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Lightweight worker_runs updater so dashboards can show status.
    Schema is intentionally simple and backward-compatible-ish.
    """
    from datetime import datetime, timezone

    coll = db.get_collection("worker_runs")
    doc: Dict[str, Any] = {
        "name": name,
        "stage": stage,
        "status": status,
        "last_run": datetime.now(timezone.utc),
    }
    if extra:
        doc.update(extra)
    coll.update_one(
        {"name": name, "stage": stage},
        {"$set": doc},
        upsert=True,
    )


# ============================================================
# Core runner
# ============================================================

def _decode_multiclass_proba(
    proba_vec: np.ndarray,
) -> Dict[str, float]:
    """
    Given a 1D numpy array of class probabilities (length 4),
    return a dict with per-class and aggregated scores.
    """
    if proba_vec.ndim != 1:
        proba_vec = proba_vec.ravel()

    # Pad / trim to 4 just in case
    if proba_vec.size < 4:
        proba_vec = np.pad(proba_vec, (0, 4 - proba_vec.size), constant_values=0.0)
    elif proba_vec.size > 4:
        proba_vec = proba_vec[:4]

    p_non, p_weak, p_viral, p_super = [float(x) for x in proba_vec]
    p_any = float(p_weak + p_viral + p_super)
    score_100 = int(round(p_any * 100))

    top_idx = int(np.argmax(proba_vec))
    top_label = MULTI_CLASS_LABELS[top_idx] if 0 <= top_idx < len(MULTI_CLASS_LABELS) else None

    return {
        "p_non": p_non,
        "p_weak": p_weak,
        "p_viral": p_viral,
        "p_super": p_super,
        "p_any": p_any,
        "score_100": score_100,
        "top_idx": top_idx,
        "top_label": top_label,
    }


def run_stage(
    stage_cmd: str,
    model_path: Path,
    mongo_uri: str,
    db_name: str,
    coll_name: str,
    only_missing: bool,
    force_all: bool,
    limit: Optional[int],
    worker_runs_name: Optional[str] = None,
) -> None:
    """
    Generic runner for a single stage.

    stage_cmd:
        "6h"  -> stage_key = "h6"
        "12h" -> stage_key = "h12"
        "24h" -> stage_key = "h24_validation"
    """
    if stage_cmd == "6h":
        stage_key = "h6"
    elif stage_cmd == "12h":
        stage_key = "h12"
    elif stage_cmd == "24h":
        stage_key = "h24"
    else:
        raise SystemExit(f"Unknown stage command: {stage_cmd}")

    if force_all:
        # override only_missing
        only_missing = False

    # Connect to Mongo
    client = MongoClient(mongo_uri)
    db = client[db_name]
    coll = db[coll_name]

    if worker_runs_name:
        upsert_worker_run(db, worker_runs_name, stage_key, "running")

    # Load model
    print(f"[CFG] Stage={stage_cmd}, model={model_path}")
    model = joblib.load(model_path)

    # Decide feature list per stage
    if stage_cmd == "6h":
        feature_cols = HARDER_FEATURES_6H
    elif stage_cmd == "12h":
        feature_cols = HARDER_FEATURES_12H
    else:  # 24h
        ensure_24h_feature_list(model)
        feature_cols = FEATURES_24H_FULL

    cursor = build_cursor(coll, stage_key=stage_key, only_missing=only_missing, limit=limit)

    total = 0
    updated = 0

    for rec in cursor:
        total += 1
        vid = rec.get("_id")

        # Age gating
        age_hrs = compute_age_hours(rec)
        if not np.isfinite(age_hrs):
            continue
        if stage_cmd == "6h" and age_hrs < 6.0:
            continue
        if stage_cmd == "12h" and age_hrs < 12.0:
            continue
        if stage_cmd == "24h" and age_hrs < 20.0:
            # allow slightly early 24h to catch "almost 24h" videos
            continue

        agg = aggregate_0_24h(rec)
        if agg is None:
            print(f"[SKIP] _id={vid}: aggregation failed")
            continue

        if stage_cmd == "6h":
            feat_dict = build_features_6h(agg)
        elif stage_cmd == "12h":
            feat_dict = build_features_12h(agg)
        else:
            feat_dict = build_features_24h(rec, agg)

        # Build X row
        x = np.array([[float(feat_dict.get(c, 0.0)) for c in feature_cols]], dtype=np.float32)

        # Predict probabilities
        proba_vec = model.predict_proba(x)[0]
        proba_info = _decode_multiclass_proba(proba_vec)

        ensure_viral_flags(rec)
        ml_viral = rec["ml_flags"]["viral_v2"]
        now_iso = utc_now_iso()

        set_fields: Dict[str, Any] = {
            "ml_flags.viral_v2.model_version": MODEL_VERSION,
            "ml_flags.viral_v2.label_rule_version": LABEL_RULE_VERSION,
        }

        if stage_cmd == "6h":
            set_fields.update(
                {
                    "ml_flags.viral_v2.h6.score_proba": proba_info["p_any"],
                    "ml_flags.viral_v2.h6.score_100": proba_info["score_100"],
                    "ml_flags.viral_v2.h6.evaluated_at": now_iso,
                    # multiclass extras
                    "ml_flags.viral_v2.h6.top_class": proba_info["top_label"],
                    "ml_flags.viral_v2.h6.proba_non": proba_info["p_non"],
                    "ml_flags.viral_v2.h6.proba_weak": proba_info["p_weak"],
                    "ml_flags.viral_v2.h6.proba_viral": proba_info["p_viral"],
                    "ml_flags.viral_v2.h6.proba_super": proba_info["p_super"],
                }
            )

        elif stage_cmd == "12h":
            set_fields.update(
                {
                    "ml_flags.viral_v2.h12.score_proba": proba_info["p_any"],
                    "ml_flags.viral_v2.h12.score_100": proba_info["score_100"],
                    "ml_flags.viral_v2.h12.evaluated_at": now_iso,
                    # multiclass extras
                    "ml_flags.viral_v2.h12.top_class": proba_info["top_label"],
                    "ml_flags.viral_v2.h12.proba_non": proba_info["p_non"],
                    "ml_flags.viral_v2.h12.proba_weak": proba_info["p_weak"],
                    "ml_flags.viral_v2.h12.proba_viral": proba_info["p_viral"],
                    "ml_flags.viral_v2.h12.proba_super": proba_info["p_super"],
                }
            )

        else:  # 24h validation
            set_fields.update(
                {
                    "ml_flags.viral_v2.h24.score_proba": proba_info["p_any"],
                    "ml_flags.viral_v2.h24.score_100": proba_info["score_100"],
                    "ml_flags.viral_v2.h24.evaluated_at": now_iso,
                    # multiclass extras
                    "ml_flags.viral_v2.h24.top_class": proba_info["top_label"],
                    "ml_flags.viral_v2.h24.proba_non": proba_info["p_non"],
                    "ml_flags.viral_v2.h24.proba_weak": proba_info["p_weak"],
                    "ml_flags.viral_v2.h24.proba_viral": proba_info["p_viral"],
                    "ml_flags.viral_v2.h24.proba_super": proba_info["p_super"],
                }
            )

        res = coll.update_one({"_id": vid}, {"$set": set_fields})
        if res.modified_count:
            updated += 1

        if total % 50 == 0:
            print(
                f"[{stage_cmd}] processed={total:,}, updated={updated:,} "
                f"(last _id={vid}, score={proba_info['score_100']}, age_hrs={age_hrs:.2f})"
            )

    print(
        f"[DONE-{stage_cmd}] total scanned={total:,}, docs updated={updated:,}"
    )

    if worker_runs_name:
        upsert_worker_run(
            db,
            worker_runs_name,
            stage_key,
            "ok",
            extra={"docs_scanned": total, "docs_updated": updated},
        )


# ============================================================
# CLI
# ============================================================

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        description="Run viral multiclass models (6h / 12h / 24h) on MongoDB."
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "stage",
            choices=["6h", "12h", "24h"],
            help="Stage to run (6h / 12h / 24h).",
        )
        sp.add_argument(
            "--mongo-uri",
            default=DEFAULT_MONGO_URI,
            help=f"MongoDB URI (default: {DEFAULT_MONGO_URI})",
        )
        sp.add_argument(
            "--db",
            default=DEFAULT_DB_NAME,
            help=f"Database name (default: {DEFAULT_DB_NAME})",
        )
        sp.add_argument(
            "--collection",
            default=DEFAULT_COLLECTION,
            help=f"Collection name (default: {DEFAULT_COLLECTION})",
        )
        group = sp.add_mutually_exclusive_group()
        group.add_argument(
            "--only-missing",
            action="store_true",
            help="Only score documents where score_proba for this stage is missing/None.",
        )
        group.add_argument(
            "--force-all",
            action="store_true",
            help="Ignore existing scores and recompute for all eligible docs.",
        )
        sp.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Optional hard limit on number of documents to scan.",
        )

    # Single subcommand "run"
    run_parser = sub.add_parser("run", help="Run a given stage.")
    add_common(run_parser)

    # Backward-compat shim: allow `python -m worker.viral_prediction_core 6h`
    # without the explicit "run" subcommand.
    # If argv is of the form ["6h", ...] rewrite it to ["run", "6h", ...].
    if argv and argv[0] in {"6h", "12h", "24h"}:
        argv = ["run", *argv]

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    if args.cmd != "run":
        raise SystemExit(f"Unexpected cmd: {args.cmd}")

    stage_cmd: str = args.stage

    if stage_cmd == "6h":
        model_path = MODEL_6H_PATH
    elif stage_cmd == "12h":
        model_path = MODEL_12H_PATH
    elif stage_cmd == "24h":
        model_path = MODEL_24H_PATH
    else:  # pragma: no cover
        raise SystemExit(f"Unknown stage: {stage_cmd}")

    run_stage(
        stage_cmd=stage_cmd,
        model_path=model_path,
        mongo_uri=args.mongo_uri,
        db_name=args.db,
        coll_name=args.collection,
        only_missing=args.only_missing,
        force_all=args.force_all,
        limit=args.limit,
        worker_runs_name="viral_prediction_core",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
