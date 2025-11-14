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

# warning disables
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=InconsistentVersionWarning)

# --- model loaders ---
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

# ---- helpers ----
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
    if (s[0] == '{' and s[-1] == '}') or (s[0] == '[' and s[-1] == ']'):
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
    for k in dotted.split('.'):
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
    Tính số giờ từ publishedAt đến snapshot cuối cùng.
    Dùng để đảm bảo chỉ gán cờ khi video đã >= 6 giờ.
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

    snaps = _loads_jsonish(rec.get("stats_snapshots")) or _loads_jsonish(rec.get("stats.snapshots"))
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

# --- single-record feature aggregator (≤6h) ---
FEATURE_ORDER = [
    'views_10m','views_30m','views_1h','views_3h','views_6h',
    'likes_10m','likes_30m','likes_1h','likes_3h','likes_6h',
    'comms_10m','comms_30m','comms_1h','comms_3h','comms_6h',
    'g_10m_to_1h','g_1h_to_3h','g_3h_to_6h','g_1h_to_6h',
    'slope_views_10m_to_1h','slope_views_1h_to_6h',
    'like_rate_6h','comm_rate_6h','durationSec'
]

def features_6h(rec: dict) -> Optional[dict]:
    rec = _loads_jsonish(rec) if isinstance(rec, str) else rec
    if not isinstance(rec, dict):
        return None

    sn = _loads_jsonish(rec.get('snippet'))
    if isinstance(sn, dict):
        pub = sn.get('publishedAt')
        duration = sn.get('durationSec')
        length_bucket = sn.get('lengthBucket')
    else:
        pub = _dig(rec, 'snippet.publishedAt') or rec.get('publishedAt')
        duration = _dig(rec, 'snippet.durationSec') or rec.get('durationSec')
        length_bucket = _dig(rec, 'snippet.lengthBucket') or rec.get('lengthBucket')

    if not pub:
        return None
    t0 = to_datetime(pub, utc=True, errors='coerce')
    if t0 is None or str(t0) == 'NaT':
        return None

    snaps = _loads_jsonish(rec.get('stats_snapshots')) or _loads_jsonish(rec.get('stats.snapshots'))
    if not isinstance(snaps, list) or not snaps:
        return None

    th, v, l, c = [], [], [], []
    for s in snaps:
        s = _loads_jsonish(s) if isinstance(s, str) else s
        if not isinstance(s, dict):
            continue
        ts = s.get('ts') or s.get('timestamp') or s.get('time')
        if not ts:
            continue
        t = to_datetime(ts, utc=True, errors='coerce')
        if t is None or str(t) == 'NaT':
            continue
        h = (t - t0).total_seconds() / 3600.0
        if h < -0.1:
            continue
        th.append(h)
        v.append(_safe_int(s.get('viewCount')))
        l.append(_safe_int(s.get('likeCount')))
        c.append(_safe_int(s.get('commentCount')))

    if not th:
        return None
    arr = sorted(zip(th, v, l, c), key=lambda z: z[0])
    th = [a[0] for a in arr]
    v = [a[1] for a in arr]
    l = [a[2] for a in arr]
    c = [a[3] for a in arr]

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
    g_1_3  = (v3 + EPS) / (v1 + EPS)
    g_3_6  = (v6_ + EPS) / (v3 + EPS)
    g_1_6  = (v6_ + EPS) / (v1 + EPS)
    slope_1_6 = (v6_ - v1) / max(5.0, EPS)
    slope_0_1 = (v1 - v10) / max(0.8333, EPS)
    like_rate_6 = l6_ / max(v6_, 1.0)
    comm_rate_6 = c6_ / max(v6_, 1.0)

    return {
        "views_10m": v10, "views_30m": v30, "views_1h": v1, "views_3h": v3, "views_6h": v6_,
        "likes_10m": l10, "likes_30m": l30, "likes_1h": l1, "likes_3h": l3, "likes_6h": l6_,
        "comms_10m": c10, "comms_30m": c30, "comms_1h": c1, "comms_3h": c3, "comms_6h": c6_,
        "g_10m_to_1h": g_10_1, "g_1h_to_3h": g_1_3, "g_3h_to_6h": g_3_6, "g_1h_to_6h": g_1_6,
        "slope_views_10m_to_1h": slope_0_1, "slope_views_1h_to_6h": slope_1_6,
        "like_rate_6h": like_rate_6, "comm_rate_6h": comm_rate_6,
        "durationSec": float(duration) if duration is not None else 0.0,
        "lengthBucket": (str(length_bucket).lower() if length_bucket is not None else None),
    }

def load_model(path: str):
    # XGBoost native
    if path.endswith(".xgb") and XGB_AVAILABLE:
        m = xgb.XGBClassifier()
        m.load_model(path)
        return m, "xgb"
    # If sklearn joblib
    if path.endswith(".joblib") and SK_AVAILABLE:
        m = joblib.load(path)
        return m, "sk"
    raise RuntimeError(f"Unsupported model format or missing packages: {path}")

def score_one(model, feat: dict) -> float:
    """
    feat: dict từ features_6h(), gồm numeric features + lengthBucket.
    Dùng model.feature_names_in_ để build vector đúng thứ tự & đủ số cột.
    """
    # Trường hợp sklearn LogisticRegression / tree model… có tên cột
    if hasattr(model, "feature_names_in_"):
        names = list(model.feature_names_in_)
        row = []
        len_raw = (feat.get("lengthBucket") or "").lower()

        for name in names:
            if name.startswith("len_"):
                # one-hot lengthBucket
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

    # Fallback nếu model không có feature_names_in_ (model cũ, không lưu tên cột)
    NUMERIC_FEATURES = [
        "views_10m", "views_30m", "views_1h", "views_3h", "views_6h",
        "likes_10m", "likes_30m", "likes_1h", "likes_3h", "likes_6h",
        "comms_10m", "comms_30m", "comms_1h", "comms_3h", "comms_6h",
        "g_10m_to_1h", "g_1h_to_3h", "g_3h_to_6h", "g_1h_to_6h",
        "slope_views_10m_to_1h", "slope_views_1h_to_6h",
        "like_rate_6h", "comm_rate_6h", "durationSec",
    ]
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

def main():
    ap = argparse.ArgumentParser("low_quality_autoflag")

    # Ưu tiên CLI, nếu không có thì lấy từ .env
    ap.add_argument(
        "--mongo-uri",
        default=os.getenv("MONGO_URI"),
        help="MongoDB URI (override env MONGO_URI)"
    )
    ap.add_argument(
        "--db",
        default=os.getenv("MONGO_DB"),
        help="Database name (override env MONGO_DB)"
    )
    ap.add_argument(
        "--col",
        default=os.getenv("MONGO_COL_VIDEOS", "videos"),
        help="Collection name (default from env MONGO_COL_VIDEOS or 'videos')"
    )
    ap.add_argument(
        "--model",
        default=os.getenv("LOWQ_MODEL_PATH"),
        help="Model path (override env LOWQ_MODEL_PATH)"
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=float(os.getenv("LOWQ_THRESHOLD", "0.281"))
    )
    ap.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("LOWQ_BATCH_SIZE", "500"))
    )
    ap.add_argument(
        "--only-missing",
        action="store_true",
        help="Only docs without ml_flags.low_quality_v3_6h.updated_at"
    )
    ap.add_argument(
        "--stop-if-low",
        dest="stop_if_low",
        action="store_true",
        default=True,
        help="(default: ON) If low then stop tracking & set stop_reason"
    )
    
    ap.add_argument(
        "--no-stop-if-low",
        dest="stop_if_low",
        action="store_false",
        help="Disable stopping videos even if low_quality"
    )

    args = ap.parse_args()

    # Tự check nếu thiếu env + CLI
    if not args.mongo_uri:
        ap.error("Missing Mongo URI: pass --mongo-uri or set MONGO_URI in .env")
    if not args.db:
        ap.error("Missing DB name: pass --db or set MONGO_DB in .env")
    if not args.model:
        ap.error("Missing model path: pass --model or set LOWQ_MODEL_PATH in .env")

    model, kind = load_model(args.model)
    print(f"[INFO] Loaded model ({kind}): {args.model}")
    print(f"[INFO] Threshold: {args.threshold}")

    mc = MongoClient(args.mongo_uri)
    col = mc[args.db][args.col]

    q = {
        "stats_snapshots.0": {"$exists": True},
        "tracking.status": "tracking",   # chỉ lấy video đang tracking
    }

    if args.only_missing:
        # Chỉ lấy những video chưa từng chấm hoặc updated_at vẫn là null
        q["$or"] = [
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
    skipped_age = 0
    skipped_feat = 0
    scored = 0
    low_count = 0

    for doc in cur:
        n += 1
        if n % 1000 == 0:
            print(
                f"[DEBUG] scanned={n:,} "
                f"skipped_age_lt_6h={skipped_age:,} "
                f"skipped_no_feat={skipped_feat:,} "
                f"scored={scored:,} low={low_count:,}",
                flush=True,
            )

        # 🔒 Chỉ gán cờ nếu video đã >= 6 giờ
        age_h = _age_hours(doc)
        if age_h is None or age_h < 6.0 - 1e-6:
            skipped_age += 1
            continue

        x = features_6h(doc)
        if x is None:
            skipped_feat += 1
            continue

        score = score_one(model, x)
        is_low = bool(score >= args.threshold)
        scored += 1
        if is_low:
            low_count += 1
        
        tracking = doc.get("tracking") or {}
        cur_status = tracking.get("status")

        update = {
            "ml_flags.low_quality_v3_6h.score": float(round(score, 6)),
            "ml_flags.low_quality_v3_6h.threshold": float(args.threshold),
            "ml_flags.low_quality_v3_6h.is_low": is_low,
            "ml_flags.low_quality_v3_6h.updated_at": _now_utc_iso(),
        }
        
        # Only stop tracking if video is still actively tracking
        if args.stop_if_low and is_low and cur_status not in ("complete", "stopped"):
            update["tracking.status"] = "stopped"
            update["tracking.stop_reason"] = "ml.low_quality_v3_6h"

        buf.append(UpdateOne({"_id": doc["_id"]}, {"$set": update}, upsert=False))
        if len(buf) >= args.batch_size:
            res = col.bulk_write(buf, ordered=False)
            n_up += res.modified_count
            buf.clear()
            print(f"[INFO] processed={n:,} updated={n_up:,} (scored={scored:,}, low={low_count:,})")

    if buf:
        res = col.bulk_write(buf, ordered=False)
        n_up += res.modified_count

    print(
        "[DONE] "
        f"total_docs={n:,} "
        f"total_updated={n_up:,} "
        f"scored={scored:,} low={low_count:,} "
        f"skipped_age_lt_6h={skipped_age:,} "
        f"skipped_no_feat={skipped_feat:,}"
    )


if __name__ == "__main__":
    main()
