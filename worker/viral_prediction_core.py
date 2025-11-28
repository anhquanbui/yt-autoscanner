# -*- coding: utf-8 -*-
"""
worker.viral_prediction_core

Core logic để chạy 3 mô hình viral (6h, 12h, 24h) trực tiếp trên MongoDB
và ghi kết quả vào ml_flags.viral_v2.*

- 6h  : early signal (ml_flags.viral_v2.h6)
- 12h : confirmation (ml_flags.viral_v2.h12)
- 24h : validator       (ml_flags.viral_v2.h24_validation)

CLI:
    python -m worker.viral_prediction_core 6h
    python -m worker.viral_prediction_core 12h
    python -m worker.viral_prediction_core 24h

    # ví dụ: chỉ tính cho video còn thiếu score
    python -m worker.viral_prediction_core 6h --only-missing

    # ví dụ: ép tính lại tất cả, bất kể tracking.status hay đã có score
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

try:
    import orjson as _orjson
except Exception:  # fallback nếu chưa có orjson
    _orjson = None


# ============================================================
# CONFIG
# ============================================================

# Mongo
DEFAULT_MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")
DEFAULT_DB_NAME = os.getenv("MONGO_DB", "ytscan")
DEFAULT_COLLECTION = os.getenv("MONGO_VIDEOS_COLLECTION", "videos")

# Model paths
MODEL_DIR = Path(os.getenv("VIRAL_MODEL_DIR", "models/viral"))

MODEL_6H_PATH = Path(
    os.getenv("VIRAL_MODEL_6H", MODEL_DIR / "viral_xgb_6h.joblib")
)
MODEL_12H_PATH = Path(
    os.getenv("VIRAL_MODEL_12H", MODEL_DIR / "viral_xgb_12h.joblib")
)
MODEL_24H_PATH = Path(
    os.getenv("VIRAL_MODEL_24H", MODEL_DIR / "viral_xgb_24h.joblib")
)

# version metadata để ghi vào ml_flags.viral_v2
MODEL_VERSION = 1
LABEL_RULE_VERSION = 1

# ============================================================
# Aggregation helpers (copy & simplify từ training 6h/12h)
# ============================================================


def _loads_fast(s: Any) -> Any:
    """Fast JSON loader supporting dict / list / JSON string / None."""
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
    """Safe nested dict getter using dotted paths."""
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
    """Convert to int, fallback to 0 on failure."""
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return 0


def _first_leq(xs: List[float], ys: List[int], target: float) -> float:
    """
    Given sorted xs (hours) and ys (values),
    return the last y where x <= target.
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
    Aggregate stats into 0–24h buckets cho 1 video.
    Copy từ _aggregate_one trong notebook training 6h/12h.

    Trả về dict chứa các cột:
      - views_*, likes_*, comms_*
      - g_*, slope_views_*
      - like_rate_*, comm_rate_*
      - durationSec, lengthBucket
    """
    if isinstance(rec, str):
        rec = _loads_fast(rec)
    if not isinstance(rec, dict):
        return None

    # --- Basic snippet ---
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
        if h < -0.1:
            continue

        th.append(h)
        v.append(_safe_int(s.get("viewCount")))
        l.append(_safe_int(s.get("likeCount")))
        c.append(_safe_int(s.get("commentCount")))

    if not th:
        return None

    arr = sorted(zip(th, v, l, c), key=lambda z: z[0])
    th = [a[0] for a in arr]
    v = [a[1] for a in arr]
    l = [a[2] for a in arr]
    c = [a[3] for a in arr]

    targets = [0.1667, 0.5, 1.0, 3.0, 6.0, 12.0, 24.0]
    v10, v30, v1, v3, v6, v12, v24 = [_first_leq(th, v, t) for t in targets]
    l10, l30, l1, l3, l6, l12, l24 = [_first_leq(th, l, t) for t in targets]
    c10, c30, c1, c3, c6, c12, c24 = [_first_leq(th, c, t) for t in targets]

    eps = 1e-6

    # Growth
    g_10_1 = (v1 + eps) / (v10 + eps)
    g_1_3 = (v3 + eps) / (v1 + eps)
    g_3_6 = (v6 + eps) / (v3 + eps)
    g_6_12 = (v12 + eps) / (v6 + eps)
    g_12_24 = (v24 + eps) / (v12 + eps)
    g_1_6 = (v6 + eps) / (v1 + eps)
    g_6_24 = (v24 + eps) / (v6 + eps)
    g_1_24 = (v24 + eps) / (v1 + eps)

    # Slopes
    slope_10_1 = (v1 - v10) / max(0.8333, eps)
    slope_1_3 = (v3 - v1) / max(2.0, eps)
    slope_3_6 = (v6 - v3) / max(3.0, eps)
    slope_6_12 = (v12 - v6) / max(6.0, eps)
    slope_12_24 = (v24 - v12) / max(12.0, eps)
    slope_1_6 = (v6 - v1) / max(5.0, eps)
    slope_6_24 = (v24 - v6) / max(18.0, eps)

    # Engagement
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
# Feature sets (giống training 6h/12h + base 24h)
# ============================================================

HARDER_FEATURES_6H = [
    # Early views (tới 1h)
    "views_10m",
    "views_30m",
    "views_1h",
    # Early likes & comms (tới 1h)
    "likes_10m",
    "likes_30m",
    "likes_1h",
    "comms_10m",
    "comms_30m",
    "comms_1h",
    # Growth & slope cực early
    "g_10m_to_1h",
    "slope_views_10m_to_1h",
    # Một chút info 3h về engagement
    "like_rate_3h",
    "comm_rate_3h",
    # Meta
    "durationSec",
]

HARDER_FEATURES_12H = [
    # Views đến 3h
    "views_10m",
    "views_30m",
    "views_1h",
    "views_3h",
    # Likes / comms đến 3h
    "likes_10m",
    "likes_30m",
    "likes_1h",
    "likes_3h",
    "comms_10m",
    "comms_30m",
    "comms_1h",
    "comms_3h",
    # Growth & slope đến 3h
    "g_10m_to_1h",
    "g_1h_to_3h",
    "slope_views_10m_to_1h",
    "slope_views_1h_to_3h",
    # Engagement 3h
    "like_rate_3h",
    "comm_rate_3h",
    # Meta
    "durationSec",
]

# Base 24h: dùng khi model không có feature_names_in_ (fallback)
FEATURES_24H_HARD_BASE = [
    # Các feature 0–24h
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
    # lengthBucket one-hot (len_*) sẽ lấy từ model nếu có
]

# Biến global sẽ được set động từ model 24h
FEATURES_24H_FULL: List[str] = []
LEN_COLS_24H: List[str] = []
FEATURES_24H_BASE_ONLY: List[str] = []


def init_24h_features_from_model(model) -> List[str]:
    """
    Dùng metadata trong model để thiết lập:
      - FEATURES_24H_FULL
      - LEN_COLS_24H
      - FEATURES_24H_BASE_ONLY

    Ưu tiên:
      1) model.feature_list_24h (mình embed lúc training)
      2) model.feature_names_in_ (nếu có)
      3) fallback: FEATURES_24H_HARD_BASE
    """
    global FEATURES_24H_FULL, LEN_COLS_24H, FEATURES_24H_BASE_ONLY

    feats = getattr(model, "feature_list_24h", None)
    if feats is None:
        feats = getattr(model, "feature_names_in_", None)

    if feats is not None:
        FEATURES_24H_FULL = [str(f) for f in feats]
        print(
            f"[24H] Using {len(FEATURES_24H_FULL)} features from model metadata"
        )
    else:
        FEATURES_24H_FULL = FEATURES_24H_HARD_BASE
        print(
            "[24H] WARNING: model has no embedded feature list, "
            "falling back to FEATURES_24H_HARD_BASE (no len_* one-hot)."
        )

    LEN_COLS_24H = [c for c in FEATURES_24H_FULL if c.startswith("len_")]
    FEATURES_24H_BASE_ONLY = [
        c for c in FEATURES_24H_FULL if not c.startswith("len_")
    ]

    return FEATURES_24H_FULL


# ============================================================
# Meta feature helpers cho 24h
# ============================================================


def build_meta_features_for_24h(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Tạo isShorts, title_len, desc_len, hashtag_count, upload_hour, upload_dow."""
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
    """Dùng ml_flags.low_quality_* để tạo lowq_* feature cho 24h."""
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
    Tạo one-hot lengthBucket theo danh sách LEN_COLS_24H.
    Nếu feature-list không có len_* thì trả về dict rỗng.
    """
    if not LEN_COLS_24H:
        return {}

    out = {c: 0.0 for c in LEN_COLS_24H}
    if length_bucket is None:
        key = "len_nan"
    else:
        key = f"len_{length_bucket}"

    if key not in out:
        # fallback: len_other hoặc len_nan nếu có
        if "len_other" in out:
            key = "len_other"
        elif "len_nan" in out:
            key = "len_nan"
        else:
            return out

    out[key] = 1.0
    return out


# ============================================================
# Build feature vectors cho từng stage
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


def build_features_24h(
    rec: Dict[str, Any], agg: Dict[str, Any]
) -> Dict[str, float]:
    """
    Build full vector cho model 24h theo FEATURES_24H_FULL.
    Kết hợp:
      - agg 0–24h
      - meta (isShorts, title_len, ...)
      - low_quality features
      - len_* one-hot (nếu có trong FEATURES_24H_FULL)
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
            # nếu cột không tồn tại (trường hợp missing hoặc NaN) -> 0
            row[col] = 0.0

    # append len_* nếu cần
    for col, val in len_dummy.items():
        row[col] = float(val)

    # đảm bảo đầy đủ tất cả cột theo FEATURES_24H_FULL
    for col in FEATURES_24H_FULL:
        row.setdefault(col, 0.0)

    return row


# ============================================================
# Mongo helpers
# ============================================================


def get_collection(
    mongo_uri: str, db_name: str, coll_name: str
):
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
    stage: 'h6', 'h12', 'h24_validation'
    only_missing: nếu True thì chỉ lấy video chưa có score_proba cho stage đó.
    KHÔNG filter theo tracking.status → thỏa yêu cầu "tính toán lại
    bất kể tracking.status".
    """
    field_score = f"ml_flags.viral_v2.{stage}.score_proba"

    query: Dict[str, Any] = {
        "stats_snapshots.0": {"$exists": True},
    }
    if only_missing:
        query[field_score] = None  # match cả missing lẫn null

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
    }

    cursor = coll.find(query, projection=projection)
    if limit is not None and limit > 0:
        cursor = cursor.limit(limit)
    return cursor


def ensure_viral_flags(rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Đảm bảo rec["ml_flags"]["viral_v2"] tồn tại với cấu trúc default.
    (hàm này chỉ tạo object Python tạm thời; update thực sẽ qua $set)
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
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Core runners cho từng stage
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
) -> None:
    """
    Generic runner cho 1 stage:
      - stage: 'h6', 'h12', 'h24_validation'
      - model_path: đường dẫn .joblib tương ứng
      - feature_cols: danh sách cột theo đúng thứ tự training
      - build_features_fn(rec, agg) -> dict[col] = value
    """
    if not model_path.exists():
        raise SystemExit(f"[ERROR] Model file not found: {model_path}")

    print(
        f"[CFG] Stage={stage}, model={model_path}, "
        f"only_missing={only_missing}, force_all={force_all}, limit={limit}"
    )

    model = joblib.load(model_path)

    # Với 24h: lấy danh sách cột trực tiếp từ model.feature_names_in_
    if stage == "h24_validation":
        feature_cols = init_24h_features_from_model(model)

    coll = get_collection(mongo_uri, db_name, coll_name)

    # Nếu force_all=True → bỏ qua only_missing
    cursor = fetch_candidates(
        coll, stage=stage, only_missing=(only_missing and not force_all), limit=limit
    )

    total = 0
    updated = 0

    for rec in cursor:
        total += 1
        vid = rec.get("_id")

        agg = aggregate_0_24h(rec)
        if agg is None:
            print(f"[SKIP] _id={vid}: aggregation failed")
            continue

        # build feature row
        feat_dict = build_features_fn(rec, agg)
        # đảm bảo thứ tự & đủ cột
        x = np.array([[feat_dict.get(c, 0.0) for c in feature_cols]], dtype=np.float32)

        proba = float(model.predict_proba(x)[0, 1])
        score_100 = int(round(proba * 100))

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
                f"(last _id={vid}, score={score_100})"
            )

    print(
        f"[DONE-{stage}] total scanned={total:,}, docs updated={updated:,}, "
        f"model={model_path}"
    )
    
    # ---- NEW: ghi worker_runs ----
    from datetime import datetime, timezone
    coll.database["worker_runs"].update_one(
    {"name": f"viral_prediction_{stage}"},
    {"$set": {"last_run": datetime.now(timezone.utc).isoformat()}},
    upsert=True,
    )
    # -------------------------------


# ============================================================
# CLI
# ============================================================


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
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
            help="Chỉ tính cho video chưa có score_proba cho stage tương ứng.",
        )
        subparser.add_argument(
            "--force-all",
            action="store_true",
            help=(
                "Bỏ qua only-missing, ép tính lại tất cả video đủ dữ liệu, "
                "bất kể tracking.status hay đã có score trước đó."
            ),
        )
        subparser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Giới hạn số video (debug). Mặc định: không giới hạn.",
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
        )

    elif args.cmd == "24h":
        # feature_cols sẽ bị override bên trong run_stage bằng model.feature_names_in_
        run_stage(
            stage="h24_validation",
            model_path=MODEL_24H_PATH,
            feature_cols=[],  # placeholder, sẽ set lại từ model
            build_features_fn=build_features_24h,
            mongo_uri=args.mongo_uri,
            db_name=args.db,
            coll_name=args.collection,
            only_missing=args.only_missing,
            force_all=args.force_all,
            limit=args.limit,
        )

    else:  # pragma: no cover
        raise SystemExit(f"Unknown command: {args.cmd}")


if __name__ == "__main__":  # pragma: no cover
    main()
