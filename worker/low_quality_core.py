#!/usr/bin/env python3
"""
low_quality_core.py

Core logic for low-quality autoflag worker, supporting 3 modes:

- mode="both"    : 3h window + 6h window (3h for [3h,6h), 6h for >=6h)
- mode="3h-only" : only run 3h model for videos with 3h <= age < 6h
- mode="6h-only" : only run 6h model for videos with age >= 6h

Extra:
- --include-all-status  : bỏ filter tracking.status="tracking"
- --status-in ...       : lọc theo nhiều status cụ thể (tracking, complete, stopped,...)

Intended usage:
  - Thin wrappers can import `run_low_quality` and call it with a chosen mode.
  - This file can still be used as a CLI script with `--mode`.

"""

from __future__ import annotations

import os
import sys
import json
import math
import argparse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
from pandas import to_datetime
from pymongo import MongoClient, UpdateOne

from sklearn.exceptions import InconsistentVersionWarning
import warnings

# Centralized env loader (shared with other workers)
from config.env import load_env, get_env

# Ensure .env is loaded once (parent/.env → project/.env → subdirs, recursive)
load_env()

# ==========================
# Optional envs (kept for compatibility)
# ==========================

ENV_MODEL_3H = get_env("LOWQ_MODEL_3H_PATH")
ENV_MODEL_6H = get_env("LOWQ_MODEL_6H_PATH")

ENV_THR_3H = get_env("LOWQ_THRESHOLD_3H") or get_env("LOWQ_MODEL_3H_THRESHOLD")
ENV_THR_6H = get_env("LOWQ_THRESHOLD_6H") or get_env("LOWQ_MODEL_6H_THRESHOLD")

ENV_ENABLED_3H = (get_env("LOWQ_3H_ENABLED", "true") or "true").lower() == "true"
ENV_ENABLED_6H = (get_env("LOWQ_6H_ENABLED", "true") or "true").lower() == "true"

ENV_ONLY_MISSING_3H = (get_env("LOWQ_3H_ONLY_MISSING", "true") or "true").lower() == "true"
ENV_ONLY_MISSING_6H = (get_env("LOWQ_6H_ONLY_MISSING", "true") or "true").lower() == "true"

ENV_STOP_3H = (get_env("LOWQ_3H_STOP_IF_LOW", "true") or "true").lower() == "true"
ENV_STOP_6H = (get_env("LOWQ_6H_STOP_IF_LOW", "true") or "true").lower() == "true"

# Disable noisy warnings (e.g., sklearn version mismatch)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=InconsistentVersionWarning)

# ==========================
# Mongo logging helper
# ==========================

def log_worker_run(worker_name: str, extra: dict | None = None):
    """Upsert one document in `worker_runs` to record last run (success or error)."""
    try:
        # Ensure env is loaded (idempotent)
        load_env()

        mongo_uri = get_env("MONGO_URI", "mongodb://localhost:27017/ytscan")
        db_name_env = get_env("MONGO_DB")

        if db_name_env:
            db_name = db_name_env
        else:
            tail = mongo_uri.rsplit("/", 1)[-1]
            db_name = tail.split("?", 1)[0] or "ytscan"

        client = MongoClient(mongo_uri)
        db = client[db_name]

        payload = {
            "name": worker_name,
            "last_run": datetime.now(timezone.utc),
        }
        if extra:
            payload.update(extra)

        db.worker_runs.update_one(
            {"name": worker_name},
            {"$set": payload},
            upsert=True,
        )
    except Exception as e:
        print(f"[WARN] Failed to log worker run for {worker_name}: {e}", file=sys.stderr)


# ==========================
# Model loaders
# ==========================

XGB_AVAILABLE = False
try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except Exception:
    pass

SK_AVAILABLE = False
try:
    import joblib
    SK_AVAILABLE = True
except Exception:
    pass

EPS = 1e-6


# ==========================
# Generic helpers
# ==========================

def _now_utc_iso() -> str:
    """Return current UTC time as ISO-8601 string (timezone-aware)."""
    # Use timezone-aware datetime to avoid DeprecationWarning and be explicit
    return datetime.now(timezone.utc).isoformat()


def _safe_int(x):
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return 0


def _loads_jsonish(x):
    if isinstance(x, (dict, list)):
        return x
    if not isinstance(x, str):
        return x
    s = x.strip()
    if not s:
        return None
    if (s[0] == "{" and s[-1] == "}") or (s[0] == "[" and s[-1] == "]"):
        try:
            import orjson as _orjson
            return _orjson.loads(s)
        except Exception:
            try:
                import json as _json
                return _json.loads(s)
            except Exception:
                return None
    return x


def _dig(d: dict, dotted: str):
    if not isinstance(d, dict):
        return None
    if dotted in d:
        return d[dotted]
    cur = d
    for k in dotted.split("."):
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    return cur


def _first_leq(xs, ys, t_target):
    out = np.nan
    for x, y in zip(xs, ys):
        if x <= t_target:
            out = y
        else:
            break
    return out


def _age_hours(rec: dict) -> Optional[float]:
    """
    Compute hours from publishedAt to last snapshot (or latest_stats_ts if present).

    This is used as a safety check to decide if record has reached >=3h / >=6h.
    With latest_stats_ts present, we avoid scanning all snapshots just to compute age.
    """
    rec = _loads_jsonish(rec) if isinstance(rec, str) else rec
    if not isinstance(rec, dict):
        return None

    # Resolve publishedAt
    sn = _loads_jsonish(rec.get("snippet"))
    if isinstance(sn, dict):
        pub = sn.get("publishedAt")
    else:
        pub = _dig(rec, "snippet.publishedAt") or rec.get("publishedAt")

    if not pub:
        return None

    t0 = to_datetime(pub, utc=True, errors="coerce")
    if t0 is None or str(t0) == "NaT":
        return None

    # --- Fast path: use latest_stats_ts if available ---
    latest = rec.get("latest_stats_ts")
    if latest is not None:
        t_latest = to_datetime(latest, utc=True, errors="coerce")
        if t_latest is not None and str(t_latest) != "NaT":
            h = (t_latest - t0).total_seconds() / 3600.0
            # Ignore obviously negative ages
            return max(h, 0.0)

    # --- Fallback: compute from stats_snapshots (old behaviour) ---
    snaps = (
        _loads_jsonish(rec.get("stats_snapshots"))
        or _loads_jsonish(rec.get("stats.snapshots"))
    )
    if not isinstance(snaps, list) or not snaps:
        return None

    max_h: Optional[float] = None
    for s in snaps:
        s = _loads_jsonish(s) if isinstance(s, str) else s
        if not isinstance(s, dict):
            continue
        ts = s.get("ts") or s.get("timestamp") or s.get("time")
        if not ts:
            continue
        t = to_datetime(ts, utc=True, errors="coerce")
        if t is None or str(t) == "NaT":
            continue
        h = (t - t0).total_seconds() / 3600.0
        if h < -0.1:
            # Ignore clearly invalid negative ages
            continue
        if (max_h is None) or (h > max_h):
            max_h = h

    return max_h



# ==========================
# Feature builders
# ==========================

FEATURE_ORDER_6H = [
    "views_10m",
    "views_30m",
    "views_1h",
    "views_3h",
    "views_6h",
    "likes_10m",
    "likes_30m",
    "likes_1h",
    "likes_3h",
    "likes_6h",
    "comms_10m",
    "comms_30m",
    "comms_1h",
    "comms_3h",
    "comms_6h",
    "g_10m_to_1h",
    "g_1h_to_3h",
    "g_3h_to_6h",
    "g_1h_to_6h",
    "slope_views_10m_to_1h",
    "slope_views_1h_to_6h",
    "like_rate_6h",
    "comm_rate_6h",
    "durationSec",
]


def _extract_time_series(rec: dict):
    """Return (t_hours, views, likes, comms) sorted by time."""
    rec = _loads_jsonish(rec) if isinstance(rec, str) else rec
    if not isinstance(rec, dict):
        return None, None, None, None

    sn = _loads_jsonish(rec.get("snippet"))
    if isinstance(sn, dict):
        pub = sn.get("publishedAt")
    else:
        pub = _dig(rec, "snippet.publishedAt") or rec.get("publishedAt")

    if not pub:
        return None, None, None, None

    t0 = to_datetime(pub, utc=True, errors="coerce")
    if t0 is None or str(t0) == "NaT":
        return None, None, None, None

    snaps = (
        _loads_jsonish(rec.get("stats_snapshots"))
        or _loads_jsonish(rec.get("stats.snapshots"))
    )
    if not isinstance(snaps, list) or not snaps:
        return None, None, None, None

    th, v, l, c = [], [], [], []
    for s in snaps:
        s = _loads_jsonish(s) if isinstance(s, str) else s
        if not isinstance(s, dict):
            continue
        ts = s.get("ts") or s.get("timestamp") or s.get("time")
        if not ts:
            continue
        t = to_datetime(ts, utc=True, errors="coerce")
        if t is None or str(t) == "NaT":
            continue
        h = (t - t0).total_seconds() / 3600.0
        if h < -0.1:
            continue
        th.append(h)
        v.append(_safe_int(s.get("viewCount")))
        l.append(_safe_int(s.get("likeCount")))
        c.append(_safe_int(s.get("commentCount")))

    if not th:
        return None, None, None, None

    arr = sorted(zip(th, v, l, c), key=lambda z: z[0])
    th = [a[0] for a in arr]
    v = [a[1] for a in arr]
    l = [a[2] for a in arr]
    c = [a[3] for a in arr]
    return th, v, l, c


def _snippet_duration_and_bucket(rec: dict):
    rec = _loads_jsonish(rec) if isinstance(rec, str) else rec
    sn = _loads_jsonish(rec.get("snippet"))
    if isinstance(sn, dict):
        duration = sn.get("durationSec")
        length_bucket = sn.get("lengthBucket")
    else:
        duration = _dig(rec, "snippet.durationSec") or rec.get("durationSec")
        length_bucket = _dig(rec, "snippet.lengthBucket") or rec.get("lengthBucket")
    return duration, length_bucket


def features_6h(rec: dict) -> Optional[dict]:
    """Aggregate stats up to 6h after publish."""
    th, v, l, c = _extract_time_series(rec)
    if th is None:
        return None

    # Use points <= 6h
    idx = [i for i, h in enumerate(th) if h <= 6.0 + 1e-9]
    if not idx:
        return None
    th6 = [th[i] for i in idx]
    v6_vals = [v[i] for i in idx]
    l6_vals = [l[i] for i in idx]
    c6_vals = [c[i] for i in idx]

    targets = [0.1667, 0.5, 1.0, 3.0, 6.0]
    v10, v30, v1, v3, v6_ = [_first_leq(th6, v6_vals, t) for t in targets]
    l10, l30, l1, l3, l6_ = [_first_leq(th6, l6_vals, t) for t in targets]
    c10, c30, c1, c3, c6_ = [_first_leq(th6, c6_vals, t) for t in targets]

    g_10_1 = (v1 + EPS) / (v10 + EPS)
    g_1_3 = (v3 + EPS) / (v1 + EPS)
    g_3_6 = (v6_ + EPS) / (v3 + EPS)
    g_1_6 = (v6_ + EPS) / (v1 + EPS)
    slope_1_6 = (v6_ - v1) / max(5.0, EPS)  # ~1h→6h
    slope_0_1 = (v1 - v10) / max(0.8333, EPS)
    like_rate_6 = l6_ / max(v6_, 1.0)
    comm_rate_6 = c6_ / max(v6_, 1.0)

    duration, length_bucket = _snippet_duration_and_bucket(rec)

    return {
        "views_10m": v10,
        "views_30m": v30,
        "views_1h": v1,
        "views_3h": v3,
        "views_6h": v6_,
        "likes_10m": l10,
        "likes_30m": l30,
        "likes_1h": l1,
        "likes_3h": l3,
        "likes_6h": l6_,
        "comms_10m": c10,
        "comms_30m": c30,
        "comms_1h": c1,
        "comms_3h": c3,
        "comms_6h": c6_,
        "g_10m_to_1h": g_10_1,
        "g_1h_to_3h": g_1_3,
        "g_3h_to_6h": g_3_6,
        "g_1h_to_6h": g_1_6,
        "slope_views_10m_to_1h": slope_0_1,
        "slope_views_1h_to_6h": slope_1_6,
        "like_rate_6h": like_rate_6,
        "comm_rate_6h": comm_rate_6,
        "durationSec": float(duration) if duration is not None else 0.0,
        "lengthBucket": (str(length_bucket).lower() if length_bucket is not None else None),
    }


def features_3h(rec: dict) -> Optional[dict]:
    """
    Aggregate stats up to 3h.
    NOTE: we reuse the same feature names as 6h version so training
    can share the same feature_cols if you want (views_6h etc will
    simply be the value at <=3h horizon).
    """
    th, v, l, c = _extract_time_series(rec)
    if th is None:
        return None

    idx = [i for i, h in enumerate(th) if h <= 3.0 + 1e-9]
    if not idx:
        return None
    th3 = [th[i] for i in idx]
    v3s = [v[i] for i in idx]
    l3s = [l[i] for i in idx]
    c3s = [c[i] for i in idx]

    targets = [0.1667, 0.5, 1.0, 3.0]
    v10, v30, v1, v3 = [_first_leq(th3, v3s, t) for t in targets]
    l10, l30, l1, l3 = [_first_leq(th3, l3s, t) for t in targets]
    c10, c30, c1, c3 = [_first_leq(th3, c3s, t) for t in targets]

    # For 3h model we still expose "6h" fields, but they actually reflect 3h horizon.
    v6_ = v3
    l6_ = l3
    c6_ = c3

    g_10_1 = (v1 + EPS) / (v10 + EPS)
    g_1_3 = (v3 + EPS) / (v1 + EPS)
    g_3_6 = 1.0  # dummy, no real 6h yet
    g_1_6 = g_1_3  # treat 3h as pseudo-6h
    slope_1_6 = (v3 - v1) / max(2.0, EPS)
    slope_0_1 = (v1 - v10) / max(0.8333, EPS)
    like_rate_6 = l3 / max(v3, 1.0)
    comm_rate_6 = c3 / max(v3, 1.0)

    duration, length_bucket = _snippet_duration_and_bucket(rec)

    return {
        "views_10m": v10,
        "views_30m": v30,
        "views_1h": v1,
        "views_3h": v3,
        "views_6h": v6_,
        "likes_10m": l10,
        "likes_30m": l30,
        "likes_1h": l1,
        "likes_3h": l3,
        "likes_6h": l6_,
        "comms_10m": c10,
        "comms_30m": c30,
        "comms_1h": c1,
        "comms_3h": c3,
        "comms_6h": c6_,
        "g_10m_to_1h": g_10_1,
        "g_1h_to_3h": g_1_3,
        "g_3h_to_6h": g_3_6,
        "g_1h_to_6h": g_1_6,
        "slope_views_10m_to_1h": slope_0_1,
        "slope_views_1h_to_6h": slope_1_6,
        "like_rate_6h": like_rate_6,
        "comm_rate_6h": comm_rate_6,
        "durationSec": float(duration) if duration is not None else 0.0,
        "lengthBucket": (str(length_bucket).lower() if length_bucket is not None else None),
    }


# ==========================
# Model + threshold helpers
# ==========================

def load_model(path: str):
    """
    Load model:
    - .joblib → sklearn (logistic regression, etc.)
    - .xgb    → XGBoost classifier
    """
    if path.endswith(".xgb") and XGB_AVAILABLE:
        m = xgb.XGBClassifier()
        m.load_model(path)
        return m, "xgb"
    if path.endswith(".joblib") and SK_AVAILABLE:
        m = joblib.load(path)
        return m, "sk"
    raise RuntimeError(f"Unsupported model format or missing packages: {path}")


def auto_threshold_from_model_or_meta(model, model_path: str, fallback: float) -> float:
    """
    Try to infer threshold from:
    - model.best_threshold_ or model.threshold_
    - sidecar meta JSON: <model>.meta.json or <stem>.meta.json
      with keys: best_threshold / threshold / thr / best_thr
    Otherwise, use fallback.
    """
    # 1) Attribute on the model
    for attr in ("best_threshold_", "threshold_", "best_thr_", "thr_"):
        if hasattr(model, attr):
            try:
                val = float(getattr(model, attr))
                print(f"[AUTO] Using model attribute {attr}={val:.4f} for threshold")
                return val
            except Exception:
                pass

    # 2) Sidecar JSONs
    candidates = []
    try:
        candidates.append(model_path + ".meta.json")
        base, ext = os.path.splitext(model_path)
        candidates.append(base + ".meta.json")
        candidates.append(base + ".json")
    except Exception:
        pass

    for p in candidates:
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                if isinstance(meta, dict):
                    for k in ("best_threshold", "threshold", "thr", "best_thr"):
                        if k in meta:
                            val = float(meta[k])
                            print(f"[AUTO] Using meta file {p}, key {k}={val:.4f} for threshold")
                            return val
        except Exception as e:
            print(f"[WARN] Failed to read meta threshold from {p}: {e}", file=sys.stderr)

    print(f"[AUTO] Fallback threshold={fallback:.4f}")
    return fallback


def score_one(model, feat: dict) -> float:
    """
    Build feature vector using model.feature_names_in_ if available,
    else use a fixed numeric feature order.
    """
    # Case 1: sklearn model with feature_names_in_
    if hasattr(model, "feature_names_in_"):
        names = list(model.feature_names_in_)
        row = []
        len_raw = (feat.get("lengthBucket") or "").lower()

        for name in names:
            if name.startswith("len_"):
                # one-hot for lengthBucket
                cat = name[len("len_"):]  # live / long / medium / short / nan ...
                if cat == "nan":
                    val = 1.0 if not len_raw else 0.0
                else:
                    val = 1.0 if len_raw == cat else 0.0
            else:
                v = feat.get(name, 0.0)
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    v = 0.0
                val = float(v)
            row.append(val)

        X = np.array([row], dtype=np.float32)

        if hasattr(model, "predict_proba"):
            p = model.predict_proba(X)[:, 1]
            return float(p[0])

        y = model.predict(X)
        y = float(y)
        return max(0.0, min(1.0, y))

    # Case 2: legacy models without feature_names_in_
    NUMERIC_FEATURES = FEATURE_ORDER_6H
    row = []
    for k in NUMERIC_FEATURES:
        v = feat.get(k, 0.0)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            v = 0.0
        row.append(float(v))
    X = np.array([row], dtype=np.float32)

    if hasattr(model, "predict_proba"):
        p = model.predict_proba(X)[:, 1]
        return float(p[0])

    y = model.predict(X)
    y = float(y)
    return max(0.0, min(1.0, y))


# ==========================
# Core worker runner
# ==========================

def build_query(
    mode: str,
    only_missing: bool,
    include_all_status: bool,
    status_in: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Build Mongo query based on mode + only_missing + status semantics.

    Always:
      - Require at least one stats snapshot.
      - Optionally filter by tracking.status.
      - Optionally restrict to docs missing 3h/6h flags.
      - If latest_stats_ts is present, also filter by age window using $dateDiff:

        mode = "3h-only" → 3h <= age < 6h
        mode = "6h-only" → age >= 6h
        mode = "both"    → age >= 3h (either 3h- or 6h-window is relevant)
    """
    q: Dict[str, Any] = {
        "stats_snapshots.0": {"$exists": True},
    }

    # --- Status filter ---
    if status_in:
        q["tracking.status"] = {"$in": status_in}
    elif not include_all_status:
        q["tracking.status"] = "tracking"

    # --- Only missing flags (keep old semantics) ---
    if only_missing:
        or_list: List[Dict[str, Any]] = []

        if mode in ("3h-only", "both"):
            or_list.append({"ml_flags.low_quality_v1_3h.updated_at": {"$exists": False}})
            or_list.append({"ml_flags.low_quality_v1_3h.updated_at": None})

        if mode in ("6h-only", "both"):
            or_list.append({"ml_flags.low_quality_v3_6h.updated_at": {"$exists": False}})
            or_list.append({"ml_flags.low_quality_v3_6h.updated_at": None})

        if or_list:
            q["$or"] = or_list

    # --- Age filter using latest_stats_ts + $dateDiff (MongoDB 5+) ---
    # We only apply this optimization when latest_stats_ts is present.
    # Older docs without this field will be handled once track_once touches them.
    age_expr: Optional[Dict[str, Any]] = None

    age_diff_expr: Dict[str, Any] = {
        "$dateDiff": {
            "startDate": "$snippet.publishedAt",
            "endDate": "$latest_stats_ts",
            "unit": "hour",
        }
    }

    if mode == "3h-only":
        # 3h <= age < 6h
        age_expr = {
            "$and": [
                {"$gte": [age_diff_expr, 3]},
                {"$lt": [age_diff_expr, 6]},
            ]
        }
    elif mode == "6h-only":
        # age >= 6h
        age_expr = {"$gte": [age_diff_expr, 6]}
    elif mode == "both":
        # age >= 3h (we will still branch 3h vs 6h in Python)
        age_expr = {"$gte": [age_diff_expr, 3]}

    if age_expr is not None:
        # Ensure endDate exists, otherwise $dateDiff would error.
        q["latest_stats_ts"] = {"$exists": True}
        q["$expr"] = age_expr

    return q



def run_low_quality(
    *,
    mode: str,
    mongo_uri: str,
    db_name: str,
    col_name: str,
    model_3h_path: str,
    model_6h_path: str,
    thr3: Optional[float] = None,
    thr6: Optional[float] = None,
    batch_size: int = 500,
    only_missing: bool = False,
    stop_if_low: bool = True,
    include_all_status: bool = False,
    status_in: Optional[List[str]] = None,
):
    """
    Core runner for low-quality ML flags.

    mode:
      - "both"    : use 3h model for age in [3h, 6h) + 6h model for age >= 6h
      - "3h-only" : only use 3h model for age in [3h, 6h)
      - "6h-only" : only use 6h model for age >= 6h

    include_all_status:
      - True  -> do not filter by tracking.status; score videos with any status (tracking, complete, stopped, etc.)
      - False -> if status_in is not set, default to only videos with tracking.status = "tracking"

    status_in:
      - If provided (e.g. ["tracking", "complete"]), it overrides include_all_status logic
      - The query will include: {"tracking.status": {"$in": status_in}}
    """
    mode = mode or "both"
    if mode not in ("both", "3h-only", "6h-only"):
        raise ValueError(f"Invalid mode: {mode}")

    print(f"[INFO] Mode: {mode}")

    model3 = kind3 = None
    model6 = kind6 = None
    thr3_final = None
    thr6_final = None

    # --- 3h model: load only if needed ---
    if mode in ("both", "3h-only"):
        model3, kind3 = load_model(model_3h_path)
        default_thr3 = thr3 if thr3 is not None else 0.5
        thr3_final = auto_threshold_from_model_or_meta(
            model3, model_3h_path, default_thr3
        )
        print(f"[INFO] Loaded 3h model ({kind3}): {model_3h_path}")
        print(f"[INFO] 3h threshold: {thr3_final:.4f}")

    # --- 6h model: load only if needed ---
    if mode in ("both", "6h-only"):
        model6, kind6 = load_model(model_6h_path)
        default_thr6 = thr6 if thr6 is not None else 0.5
        thr6_final = auto_threshold_from_model_or_meta(
            model6, model_6h_path, default_thr6
        )
        print(f"[INFO] Loaded 6h model ({kind6}): {model_6h_path}")
        print(f"[INFO] 6h threshold: {thr6_final:.4f}")

    mc = MongoClient(mongo_uri)
    col = mc[db_name][col_name]

    q = build_query(
        mode=mode,
        only_missing=only_missing,
        include_all_status=include_all_status,
        status_in=status_in,
    )
    print(f"[INFO] Mongo query: {q}")
    
    # Count total docs that match query for progress logging
    total_candidates = col.count_documents(q)
    print(f"[INFO] Total candidates matching query: {total_candidates:,}")

    cur = col.find(
        q,
        {
            "_id": 1,
            "snippet": 1,
            "stats_snapshots": 1,
            "stats.snapshots": 1,
            "ml_flags": 1,
            "tracking": 1,
        },
        no_cursor_timeout=True,
    )

    buf: List[UpdateOne] = []
    n = 0
    n_up = 0

    skipped_age_lt_3h = 0
    skipped_age_lt_6h = 0
    skipped_feat_3h = 0
    skipped_feat_6h = 0

    scored_3h = 0
    scored_6h = 0
    low_3h = 0
    low_6h = 0

    try:
        for doc in cur:
            n += 1
            if n % 1000 == 0:
                pct = (n / total_candidates * 100.0) if total_candidates else 0.0
                remaining = total_candidates - n if total_candidates else 0
                print(
                    f"[DEBUG] scanned={n:,}/{total_candidates:,} ({pct:.1f}%) "
                    f"remaining={remaining:,} "
                    f"skipped_age_lt_3h={skipped_age_lt_3h:,} "
                    f"skipped_age_lt_6h={skipped_age_lt_6h:,} "
                    f"skipped_feat_3h={skipped_feat_3h:,} "
                    f"skipped_feat_6h={skipped_feat_6h:,} "
                    f"scored_3h={scored_3h:,} low_3h={low_3h:,} "
                    f"scored_6h={scored_6h:,} low_6h={low_6h:,}",
                    flush=True,
                )

            age_h = _age_hours(doc)
            if age_h is None:
                # no valid age → treat as <3h for stats
                skipped_age_lt_3h += 1
                continue

            tracking = doc.get("tracking") or {}
            cur_status = tracking.get("status")

            update: Dict[str, Any] = {}

            # ======================
            # 3h model: only for [3h, 6h) if mode allows
            # ======================
            if mode in ("both", "3h-only"):
                ran_3h = False
                is_low_3h = False

                if age_h < 3.0 - 1e-6:
                    skipped_age_lt_3h += 1
                elif age_h < 6.0 - 1e-6:
                    x3 = features_3h(doc)
                    if x3 is None:
                        skipped_feat_3h += 1
                    else:
                        ran_3h = True
                        score3 = score_one(model3, x3)
                        is_low_3h = bool(score3 >= thr3_final)
                        scored_3h += 1
                        if is_low_3h:
                            low_3h += 1

                        update.update(
                            {
                                "ml_flags.low_quality_v1_3h.score": float(
                                    round(score3, 6)
                                ),
                                "ml_flags.low_quality_v1_3h.threshold": float(
                                    thr3_final
                                ),
                                "ml_flags.low_quality_v1_3h.is_low": is_low_3h,
                                "ml_flags.low_quality_v1_3h.updated_at": _now_utc_iso(),
                            }
                        )

                        # Early-stop if 3h model flags as low
                        if stop_if_low and is_low_3h and cur_status == "tracking":
                            update["tracking.status"] = "stopped"
                            update["tracking.stop_reason"] = "ml.low_quality_v1_3h"
                else:
                    # age_h >= 6h → do not run the 3h model anymore by design
                    pass

                # If the 3h model has already stopped the video → no need to run the 6h model
                if update.get("tracking.status") == "stopped":
                    buf.append(
                        UpdateOne({"_id": doc["_id"]}, {"$set": update}, upsert=False)
                    )
                    if len(buf) >= batch_size:
                        res = col.bulk_write(buf, ordered=False)
                        n_up += res.modified_count
                        buf.clear()
                        pct = (n / total_candidates * 100.0) if total_candidates else 0.0
                        remaining = total_candidates - n if total_candidates else 0
                        print(
                            f"[INFO] processed={n:,}/{total_candidates:,} ({pct:.1f}%) "
                            f"remaining={remaining:,} "
                            f"updated={n_up:,} "
                            f"(scored_3h={scored_3h:,}, low_3h={low_3h:,}, "
                            f"scored_6h={scored_6h:,}, low_6h={low_6h:,})"
                        )
                    continue

            # ======================
            # 6h model: only for age >= 6h if mode allows
            # ======================
            if mode in ("both", "6h-only"):
                if age_h < 6.0 - 1e-6:
                    skipped_age_lt_6h += 1
                else:
                    x6 = features_6h(doc)
                    if x6 is None:
                        skipped_feat_6h += 1
                    else:
                        score6 = score_one(model6, x6)
                        is_low_6h = bool(score6 >= thr6_final)
                        scored_6h += 1
                        if is_low_6h:
                            low_6h += 1

                        update.update(
                            {
                                "ml_flags.low_quality_v3_6h.score": float(
                                    round(score6, 6)
                                ),
                                "ml_flags.low_quality_v3_6h.threshold": float(
                                    thr6_final
                                ),
                                "ml_flags.low_quality_v3_6h.is_low": is_low_6h,
                                "ml_flags.low_quality_v3_6h.updated_at": _now_utc_iso(),
                            }
                        )

                        if stop_if_low and is_low_6h and cur_status == "tracking":
                            update["tracking.status"] = "stopped"
                            update["tracking.stop_reason"] = "ml.low_quality_v3_6h"

            if update:
                buf.append(
                    UpdateOne({"_id": doc["_id"]}, {"$set": update}, upsert=False)
                )
            if len(buf) >= batch_size:
                res = col.bulk_write(buf, ordered=False)
                n_up += res.modified_count
                buf.clear()
                print(
                    f"[INFO] processed={n:,} updated={n_up:,} "
                    f"(scored_3h={scored_3h:,}, low_3h={low_3h:,}, "
                    f"scored_6h={scored_6h:,}, low_6h={low_6h:,})"
                )
    finally:
        try:
            cur.close()
        except Exception:
            pass

    if buf:
        res = col.bulk_write(buf, ordered=False)
        n_up += res.modified_count

    print(
        "[DONE] "
        f"mode={mode} "
        f"total_candidates={total_candidates:,} "
        f"scanned={n:,} "
        f"total_updated={n_up:,} "
        f"scored_3h={scored_3h:,} low_3h={low_3h:,} "
        f"scored_6h={scored_6h:,} low_6h={low_6h:,} "
        f"skipped_age_lt_3h={skipped_age_lt_3h:,} "
        f"skipped_age_lt_6h={skipped_age_lt_6h:,} "
        f"skipped_feat_3h={skipped_feat_3h:,} "
        f"skipped_feat_6h={skipped_feat_6h:,}"
    )

    # Worker name for worker_runs
    if mode == "3h-only":
        worker_name = "low_quality_autoflag_3h"
    elif mode == "6h-only":
        worker_name = "low_quality_autoflag_6h"
    else:
        worker_name = "low_quality_autoflag_both"

    log_worker_run(
        worker_name,
        {
            "status": "ok",
            "mode": mode,
            "total_candidates": total_candidates,
            "total_docs": n,
            "total_updated": n_up,
            "scored_3h": scored_3h,
            "low_3h": low_3h,
            "scored_6h": scored_6h,
            "low_6h": low_6h,
            "skipped_age_lt_3h": skipped_age_lt_3h,
            "skipped_age_lt_6h": skipped_age_lt_6h,
            "skipped_feat_3h": skipped_feat_3h,
            "skipped_feat_6h": skipped_feat_6h,
            "include_all_status": include_all_status,
            "status_in": status_in,
        },
    )


# ==========================
# EXTRA: Backfill full 3h + 6h scores for any status
# ==========================

def backfill_all_scores(
    *,
    mongo_uri: str,
    db_name: str,
    col_name: str,
    model_3h_path: str,
    model_6h_path: str,
    thr3: Optional[float] = None,
    thr6: Optional[float] = None,
    batch_size: int = 500,
    status_in: Optional[List[str]] = None,
):
    """
    Backfill full scores for 3h + 6h for ANY video (tracking, complete, stopped),
    regardless of age.

    Logic:
      - If ml_flags.low_quality_v1_3h.threshold is None → compute 3h score
      - If ml_flags.low_quality_v3_6h.threshold is None → compute 6h score
      - Do NOT change tracking.status / stop_reason
      - Only compute if snapshots exist
      - Idempotent: safe to rerun anytime
    """
    print("[BACKFILL] Starting backfill-all-scores...")

    # Load models
    model3, kind3 = load_model(model_3h_path)
    model6, kind6 = load_model(model_6h_path)

    thr3_base = thr3 if thr3 is not None else 0.5
    thr6_base = thr6 if thr6 is not None else 0.5

    thr3_final = auto_threshold_from_model_or_meta(model3, model_3h_path, thr3_base)
    thr6_final = auto_threshold_from_model_or_meta(model6, model_6h_path, thr6_base)

    print(f"[BACKFILL] 3h model loaded: {model_3h_path} (thr={thr3_final})")
    print(f"[BACKFILL] 6h model loaded: {model_6h_path} (thr={thr6_final})")

    mc = MongoClient(mongo_uri)
    col = mc[db_name][col_name]

    # Query: only videos that both:
    #  - have snapshots
    #  - are missing either 3h or 6h thresholds
    q: Dict[str, Any] = {
        "stats_snapshots.0": {"$exists": True},
        "$or": [
            # Missing 3h threshold
            {"ml_flags.low_quality_v1_3h.threshold": {"$exists": False}},
            {"ml_flags.low_quality_v1_3h.threshold": None},
            # Missing 6h threshold
            {"ml_flags.low_quality_v3_6h.threshold": {"$exists": False}},
            {"ml_flags.low_quality_v3_6h.threshold": None},
        ],
    }

    cur = col.find(
        q,
        {
            "_id": 1,
            "snippet": 1,
            "stats_snapshots": 1,
            "stats.snapshots": 1,
            "ml_flags": 1,
        },
        no_cursor_timeout=True,
    )

    buf: List[UpdateOne] = []
    n = 0
    updated = 0
    scored3 = 0
    scored6 = 0
    skipped_feat_3h = 0
    skipped_feat_6h = 0

    try:
        for doc in cur:
            n += 1
            ml = doc.get("ml_flags") or {}

            update: Dict[str, Any] = {}

            # 3h block
            d3 = ml.get("low_quality_v1_3h") or {}
            need_3h = d3.get("threshold") is None

            if need_3h:
                f3 = features_3h(doc)
                if f3 is None:
                    skipped_feat_3h += 1
                else:
                    s3 = score_one(model3, f3)
                    scored3 += 1
                    update.update(
                        {
                            "ml_flags.low_quality_v1_3h.score": float(round(s3, 6)),
                            "ml_flags.low_quality_v1_3h.threshold": float(thr3_final),
                            "ml_flags.low_quality_v1_3h.is_low": bool(s3 >= thr3_final),
                            "ml_flags.low_quality_v1_3h.updated_at": _now_utc_iso(),
                        }
                    )

            # 6h block
            d6 = ml.get("low_quality_v3_6h") or {}
            need_6h = d6.get("threshold") is None

            if need_6h:
                f6 = features_6h(doc)
                if f6 is None:
                    skipped_feat_6h += 1
                else:
                    s6 = score_one(model6, f6)
                    scored6 += 1
                    update.update(
                        {
                            "ml_flags.low_quality_v3_6h.score": float(round(s6, 6)),
                            "ml_flags.low_quality_v3_6h.threshold": float(thr6_final),
                            "ml_flags.low_quality_v3_6h.is_low": bool(s6 >= thr6_final),
                            "ml_flags.low_quality_v3_6h.updated_at": _now_utc_iso(),
                        }
                    )

            if update:
                buf.append(UpdateOne({"_id": doc["_id"]}, {"$set": update}, upsert=False))

            if len(buf) >= batch_size:
                res = col.bulk_write(buf, ordered=False)
                updated += res.modified_count
                buf.clear()
                print(
                    f"[BACKFILL] processed={n:,} updated={updated:,} "
                    f"scored3={scored3:,} scored6={scored6:,} "
                    f"skipped_feat_3h={skipped_feat_3h:,} skipped_feat_6h={skipped_feat_6h:,}"
                )
    finally:
        try:
            cur.close()
        except Exception:
            pass

    if buf:
        res = col.bulk_write(buf, ordered=False)
        updated += res.modified_count

    print(
        "[BACKFILL DONE] "
        f"scanned={n:,} updated={updated:,} "
        f"scored3={scored3:,} scored6={scored6:,} "
        f"skipped_feat_3h={skipped_feat_3h:,} skipped_feat_6h={skipped_feat_6h:,}"
    )


# ==========================
# CLI entry (optional)
# ==========================

def main(argv=None):
    ap = argparse.ArgumentParser("low_quality_core (3h + 6h)")

    # Mongo / collection
    ap.add_argument(
        "--mongo-uri",
        default=get_env("MONGO_URI"),
        help="MongoDB URI (override env MONGO_URI)",
    )
    ap.add_argument(
        "--db",
        default=get_env("MONGO_DB"),
        help="Database name (override env MONGO_DB)",
    )
    ap.add_argument(
        "--col",
        default=get_env("MONGO_COL_VIDEOS", "videos"),
        help="Collection name (default from env MONGO_COL_VIDEOS or 'videos')",
    )

    # MODE
    ap.add_argument(
        "--mode",
        choices=["both", "3h-only", "6h-only"],
        default=get_env("LOWQ_MODE", "both"),
        help="Run mode: 'both' (3h [3,6) + 6h >=6), '3h-only', or '6h-only'",
    )

    # MODEL PATHS (3h + 6h)
    ap.add_argument(
        "--model-3h",
        default=get_env("LOWQ_MODEL_3H_PATH"),
        help="Model path for ≤3h (logistic joblib / xgb).",
    )
    ap.add_argument(
        "--model-6h",
        default=get_env("LOWQ_MODEL_6H_PATH") or get_env("LOWQ_MODEL_PATH"),
        help="Model path for ≤6h. If unset, fallback to LOWQ_MODEL_PATH.",
    )

    # Backward-compat: old --model behaves as 6h model
    ap.add_argument(
        "--model",
        default=get_env("LOWQ_MODEL_PATH"),
        help="[DEPRECATED] Legacy single-model path (treated as 6h model).",
    )

    # THRESHOLDS
    thr3_env = get_env("LOWQ_THRESHOLD_3H")
    thr6_env = get_env("LOWQ_THRESHOLD_6H")
    thr_legacy_env = get_env("LOWQ_THRESHOLD")

    ap.add_argument(
        "--threshold-3h",
        type=float,
        default=(float(thr3_env) if thr3_env is not None else None),
        help="Threshold for ≤3h model. If missing, auto-detect from meta.",
    )
    ap.add_argument(
        "--threshold-6h",
        type=float,
        default=(float(thr6_env) if thr6_env is not None else None),
        help="Threshold for ≤6h model. If missing, auto-detect from meta.",
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=(float(thr_legacy_env) if thr_legacy_env is not None else None),
        help="[DEPRECATED] Single threshold (used for 6h fallback only).",
    )

    # Worker configs
    ap.add_argument(
        "--batch-size",
        type=int,
        default=int(get_env("LOWQ_BATCH_SIZE", "500")),
        help="Mongo bulk_write batch size.",
    )
    ap.add_argument(
        "--only-missing",
        action="store_true",
        help="Only update docs missing 3h/6h updated_at (depending on mode).",
    )

    ap.add_argument(
        "--stop-if-low",
        dest="stop_if_low",
        action="store_true",
        default=True,
        help="Stop video if low_quality (default ON).",
    )
    ap.add_argument(
        "--no-stop-if-low",
        dest="stop_if_low",
        action="store_false",
        help="Disable stopping even if low_quality.",
    )

    ap.add_argument(
        "--include-all-status",
        action="store_true",
        help="Include videos even if tracking.status != 'tracking' (includes 'complete', 'stopped', etc.).",
    )

    ap.add_argument(
        "--status-in",
        nargs="+",
        help=(
            "Optional list of tracking.status values to include "
            "(e.g. --status-in tracking complete). "
            "If set, overrides include_all_status logic."
        ),
    )

    # NEW: backfill-all-scores CLI
    ap.add_argument(
        "--backfill-all-scores",
        action="store_true",
        help="Compute full 3h+6h scores for ANY status (training use, no tracking.status changes).",
    )

    args = ap.parse_args(argv)

    # Basic validations
    if not args.mongo_uri:
        ap.error("Missing Mongo URI: pass --mongo-uri or set MONGO_URI in .env")
    if not args.db:
        ap.error("Missing DB name: pass --db or set MONGO_DB in .env")

    model_3h_path = args.model_3h
    model_6h_path = args.model_6h or args.model

    if not model_3h_path:
        ap.error("Missing 3h model path: pass --model-3h or set LOWQ_MODEL_3H_PATH in .env")
    if not model_6h_path:
        ap.error(
            "Missing 6h model path: pass --model-6h or set LOWQ_MODEL_6H_PATH/LOWQ_MODEL_PATH in .env"
        )

    # Resolve thresholds
    thr3 = args.threshold_3h
    if thr3 is None and ENV_THR_3H is not None:
        try:
            thr3 = float(ENV_THR_3H)
        except Exception:
            pass

    thr6 = args.threshold_6h
    if thr6 is None:
        if args.threshold is not None:
            thr6 = args.threshold
        elif ENV_THR_6H is not None:
            try:
                thr6 = float(ENV_THR_6H)
            except Exception:
                thr6 = None

    # Backfill mode: compute full scores for training, do not change tracking.status
    if args.backfill_all_scores:
        backfill_all_scores(
            mongo_uri=args.mongo_uri,
            db_name=args.db,
            col_name=args.col,
            model_3h_path=model_3h_path,
            model_6h_path=model_6h_path,
            thr3=thr3,
            thr6=thr6,
            batch_size=args.batch_size,
            status_in=args.status_in,
        )
        return

    # Normal worker mode
    run_low_quality(
        mode=args.mode,
        mongo_uri=args.mongo_uri,
        db_name=args.db,
        col_name=args.col,
        model_3h_path=model_3h_path,
        model_6h_path=model_6h_path,
        thr3=thr3,
        thr6=thr6,
        batch_size=args.batch_size,
        only_missing=args.only_missing,
        stop_if_low=args.stop_if_low,
        include_all_status=args.include_all_status,
        status_in=args.status_in,
    )


if __name__ == "__main__":
    main()
