# worker/low_quality_autoflag.py
#!/usr/bin/env python3
from __future__ import annotations
import os, sys, json, math, argparse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
from pymongo import MongoClient, UpdateOne
from pandas import to_datetime

from dotenv import load_dotenv
load_dotenv()

import warnings
from sklearn.exceptions import InconsistentVersionWarning

# Disable noisy warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=InconsistentVersionWarning)

# ==========================
# Mongo logging helper
# ==========================
def log_worker_run(worker_name: str, extra: dict | None = None):
    """Upsert one document in `worker_runs` to record last run (success or error)."""
    try:
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")
        db_name_env = os.getenv("MONGO_DB")

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
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()


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
    Compute hours from publishedAt to last snapshot.
    Used to decide if record has reached >=3h / >=6h.
    """
    rec = _loads_jsonish(rec) if isinstance(rec, str) else rec
    if not isinstance(rec, dict):
        return None

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

    snaps = (
        _loads_jsonish(rec.get("stats_snapshots"))
        or _loads_jsonish(rec.get("stats.snapshots"))
    )
    if not isinstance(snaps, list) or not snaps:
        return None

    max_h = None
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
    v6 = [v[i] for i in idx]
    l6 = [l[i] for i in idx]
    c6 = [c[i] for i in idx]

    targets = [0.1667, 0.5, 1.0, 3.0, 6.0]
    v10, v30, v1, v3, v6_ = [_first_leq(th6, v6, t) for t in targets]
    l10, l30, l1, l3, l6_ = [_first_leq(th6, l6, t) for t in targets]
    c10, c30, c1, c3, c6_ = [_first_leq(th6, c6, t) for t in targets]

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
                cat = name[len("len_") :]  # live / long / medium / short / nan ...
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
# Main worker
# ==========================
def main():
    ap = argparse.ArgumentParser("low_quality_autoflag (3h + 6h)")

    # Mongo / collection
    ap.add_argument(
        "--mongo-uri",
        default=os.getenv("MONGO_URI"),
        help="MongoDB URI (override env MONGO_URI)",
    )
    ap.add_argument(
        "--db",
        default=os.getenv("MONGO_DB"),
        help="Database name (override env MONGO_DB)",
    )
    ap.add_argument(
        "--col",
        default=os.getenv("MONGO_COL_VIDEOS", "videos"),
        help="Collection name (default from env MONGO_COL_VIDEOS or 'videos')",
    )

    # Model paths
    ap.add_argument(
        "--model-3h",
        default=os.getenv("LOWQ_MODEL_3H_PATH"),
        help="Model path for ≤3h (logistic joblib).",
    )
    ap.add_argument(
        "--model-6h",
        default=os.getenv("LOWQ_MODEL_6H_PATH") or os.getenv("LOWQ_MODEL_PATH"),
        help="Model path for ≤6h (logistic joblib). If not set, fallback to LOWQ_MODEL_PATH.",
    )

    # Backward-compat: old --model treated as 6h model if provided
    ap.add_argument(
        "--model",
        default=os.getenv("LOWQ_MODEL_PATH"),
        help="[DEPRECATED] Backward-compat: used as --model-6h if --model-6h is not set.",
    )

    # Thresholds
    ap.add_argument(
        "--threshold-3h",
        type=float,
        default=None,
        help="Override threshold for ≤3h model (default: auto from training/meta).",
    )
    ap.add_argument(
        "--threshold-6h",
        type=float,
        default=None,
        help="Override threshold for ≤6h model (default: auto from training/meta or --threshold).",
    )

    # Backward-compat: old single threshold
    ap.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="[DEPRECATED] Single threshold (used as 6h fallback if --threshold-6h not set).",
    )

    ap.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("LOWQ_BATCH_SIZE", "500")),
        help="Mongo bulk_write batch size.",
    )
    ap.add_argument(
        "--only-missing",
        action="store_true",
        help="Only docs where 3h or 6h flags do not have updated_at.",
    )
    ap.add_argument(
        "--stop-if-low",
        dest="stop_if_low",
        action="store_true",
        default=True,
        help="(default: ON) If low then stop tracking & set stop_reason.",
    )
    ap.add_argument(
        "--no-stop-if-low",
        dest="stop_if_low",
        action="store_false",
        help="Disable stopping videos even if low_quality.",
    )

    args = ap.parse_args()

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
        ap.error("Missing 6h model path: pass --model-6h or set LOWQ_MODEL_6H_PATH/LOWQ_MODEL_PATH in .env")

    model3, kind3 = load_model(model_3h_path)
    model6, kind6 = load_model(model_6h_path)

    # Auto thresholds
    default_thr3 = args.threshold_3h if args.threshold_3h is not None else 0.5
    default_thr6 = (
        args.threshold_6h
        if args.threshold_6h is not None
        else (args.threshold if args.threshold is not None else 0.5)
    )

    thr3 = auto_threshold_from_model_or_meta(model3, model_3h_path, default_thr3)
    thr6 = auto_threshold_from_model_or_meta(model6, model_6h_path, default_thr6)

    print(f"[INFO] Loaded 3h model ({kind3}): {model_3h_path}")
    print(f"[INFO] 3h threshold: {thr3:.4f}")
    print(f"[INFO] Loaded 6h model ({kind6}): {model_6h_path}")
    print(f"[INFO] 6h threshold: {thr6:.4f}")

    mc = MongoClient(args.mongo_uri)
    col = mc[args.db][args.col]

    q: Dict[str, Any] = {
        "stats_snapshots.0": {"$exists": True},
        "tracking.status": "tracking",  # only actively tracking videos
    }

    if args.only_missing:
        # Only videos missing either 3h or 6h updated_at
        q["$or"] = [
            {"ml_flags.low_quality_v1_3h.updated_at": {"$exists": False}},
            {"ml_flags.low_quality_v1_3h.updated_at": None},
            {"ml_flags.low_quality_v3_6h.updated_at": {"$exists": False}},
            {"ml_flags.low_quality_v3_6h.updated_at": None},
        ]

    cur = col.find(
        q,
        {"_id": 1, "snippet": 1, "stats_snapshots": 1, "stats.snapshots": 1, "ml_flags": 1, "tracking": 1},
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

    for doc in cur:
        n += 1
        if n % 1000 == 0:
            print(
                f"[DEBUG] scanned={n:,} "
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
            skipped_age_lt_3h += 1
            continue

        tracking = doc.get("tracking") or {}
        cur_status = tracking.get("status")

        update: Dict[str, Any] = {}

        # ======================
        # First: 3h model
        # ======================
        ran_3h = False
        is_low_3h = False

        if age_h < 3.0 - 1e-6:
            skipped_age_lt_3h += 1
        else:
            x3 = features_3h(doc)
            if x3 is None:
                skipped_feat_3h += 1
            else:
                ran_3h = True
                score3 = score_one(model3, x3)
                is_low_3h = bool(score3 >= thr3)
                scored_3h += 1
                if is_low_3h:
                    low_3h += 1

                update.update(
                    {
                        "ml_flags.low_quality_v1_3h.score": float(round(score3, 6)),
                        "ml_flags.low_quality_v1_3h.threshold": float(thr3),
                        "ml_flags.low_quality_v1_3h.is_low": is_low_3h,
                        "ml_flags.low_quality_v1_3h.updated_at": _now_utc_iso(),
                    }
                )

                # Early-stop if 3h model flags as low
                if args.stop_if_low and is_low_3h and cur_status not in ("complete", "stopped"):
                    update["tracking.status"] = "stopped"
                    update["tracking.stop_reason"] = "ml.low_quality_v1_3h"

        # If 3h already stopped, no need to run 6h
        if update.get("tracking.status") == "stopped":
            buf.append(UpdateOne({"_id": doc["_id"]}, {"$set": update}, upsert=False))
            if len(buf) >= args.batch_size:
                res = col.bulk_write(buf, ordered=False)
                n_up += res.modified_count
                buf.clear()
                print(
                    f"[INFO] processed={n:,} updated={n_up:,} "
                    f"(scored_3h={scored_3h:,}, low_3h={low_3h:,}, "
                    f"scored_6h={scored_6h:,}, low_6h={low_6h:,})"
                )
            continue

        # ======================
        # Second: 6h model (only if not low at 3h)
        # ======================
        if age_h < 6.0 - 1e-6:
            skipped_age_lt_6h += 1
        else:
            x6 = features_6h(doc)
            if x6 is None:
                skipped_feat_6h += 1
            else:
                score6 = score_one(model6, x6)
                is_low_6h = bool(score6 >= thr6)
                scored_6h += 1
                if is_low_6h:
                    low_6h += 1

                update.update(
                    {
                        "ml_flags.low_quality_v3_6h.score": float(round(score6, 6)),
                        "ml_flags.low_quality_v3_6h.threshold": float(thr6),
                        "ml_flags.low_quality_v3_6h.is_low": is_low_6h,
                        "ml_flags.low_quality_v3_6h.updated_at": _now_utc_iso(),
                    }
                )

                if args.stop_if_low and is_low_6h and cur_status not in ("complete", "stopped"):
                    update["tracking.status"] = "stopped"
                    update["tracking.stop_reason"] = "ml.low_quality_v3_6h"

        if update:
            buf.append(UpdateOne({"_id": doc["_id"]}, {"$set": update}, upsert=False))
        if len(buf) >= args.batch_size:
            res = col.bulk_write(buf, ordered=False)
            n_up += res.modified_count
            buf.clear()
            print(
                f"[INFO] processed={n:,} updated={n_up:,} "
                f"(scored_3h={scored_3h:,}, low_3h={low_3h:,}, "
                f"scored_6h={scored_6h:,}, low_6h={low_6h:,})"
            )

    if buf:
        res = col.bulk_write(buf, ordered=False)
        n_up += res.modified_count

    print(
        "[DONE] "
        f"total_docs={n:,} "
        f"total_updated={n_up:,} "
        f"scored_3h={scored_3h:,} low_3h={low_3h:,} "
        f"scored_6h={scored_6h:,} low_6h={low_6h:,} "
        f"skipped_age_lt_3h={skipped_age_lt_3h:,} "
        f"skipped_age_lt_6h={skipped_age_lt_6h:,} "
        f"skipped_feat_3h={skipped_feat_3h:,} "
        f"skipped_feat_6h={skipped_feat_6h:,}"
    )

    log_worker_run(
        "low_quality_autoflag",
        {
            "status": "ok",
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
        },
    )


if __name__ == "__main__":
    main()
