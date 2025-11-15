#!/usr/bin/env python3
# tools/ml_flags_migrate.py
# Migrate videos.ml_flags to unified schema:
#   - ml_flags.viral_v1 {...}
#   - ml_flags.low_quality_v1_3h {...}
#   - ml_flags.low_quality_v3_6h {...}
#
# Hỗ trợ:
#   - Map từ legacy flat keys:
#       ml_flags.likely_viral
#       ml_flags.viral_confirmed
#       ml_flags.score
#       ml_flags.updated_at
#   - Và từ dạng trung gian đã có viral_v1 + low_quality_v3_6h.
#
# Chạy an toàn nhiều lần (idempotent).
# Có --dry-run để xem trước.

from __future__ import annotations
import os, sys, argparse
from typing import Any, Dict, Tuple

from pymongo import MongoClient, UpdateOne

LEGACY_KEYS = [
    "ml_flags.likely_viral",
    "ml_flags.viral_confirmed",
    "ml_flags.score",
    "ml_flags.updated_at",
]


def has_agg_update(server_info: Dict[str, Any]) -> bool:
    """Return True if server likely supports update with aggregation pipeline (MongoDB 4.2+)."""
    try:
        v = server_info.get("version", "0.0.0")
        major, minor, *_ = (int(x) for x in v.split("."))
        return (major > 4) or (major == 4 and minor >= 2)
    except Exception:
        return True


def _legacy_query() -> Dict[str, Any]:
    """
    Doc được coi là 'legacy' nếu:
      - CÒN bất kỳ flat key nào, HOẶC
      - CHƯA có ml_flags.low_quality_v1_3h (dạng trung gian).
    """
    return {
        "$or": [
            {"ml_flags.likely_viral": {"$exists": True}},
            {"ml_flags.viral_confirmed": {"$exists": True}},
            {"ml_flags.score": {"$exists": True}},
            {"ml_flags.updated_at": {"$exists": True}},
            {"ml_flags.low_quality_v1_3h": {"$exists": False}},
        ]
    }


def count_docs(coll, only_legacy: bool) -> Tuple[int, int]:
    q_legacy = _legacy_query()
    total = coll.estimated_document_count()
    legacy = coll.count_documents(q_legacy)
    if only_legacy:
        return legacy, legacy
    return total, legacy


def run_pipeline_update(coll, only_legacy: bool) -> Tuple[int, int]:
    """Use updateMany with aggregation pipeline; returns (matched, modified)."""
    match: Dict[str, Any] = _legacy_query() if only_legacy else {}

    pipeline = [
        {
            "$set": {
                "ml_flags": {
                    "$mergeObjects": [
                        "$ml_flags",
                        {
                            # ----- viral_v1 (giữ logic cũ) -----
                            "viral_v1": {
                                "likely": {
                                    "$ifNull": [
                                        "$ml_flags.viral_v1.likely",
                                        {
                                            "$ifNull": [
                                                "$ml_flags.likely_viral",
                                                False,
                                            ]
                                        },
                                    ]
                                },
                                "confirmed": {
                                    "$ifNull": [
                                        "$ml_flags.viral_v1.confirmed",
                                        {
                                            "$ifNull": [
                                                "$ml_flags.viral_confirmed",
                                                False,
                                            ]
                                        },
                                    ]
                                },
                                "score": {
                                    "$ifNull": [
                                        "$ml_flags.viral_v1.score",
                                        {
                                            "$ifNull": [
                                                "$ml_flags.score",
                                                0,
                                            ]
                                        },
                                    ]
                                },
                                "updated_at": {
                                    "$ifNull": [
                                        "$ml_flags.viral_v1.updated_at",
                                        "$ml_flags.updated_at",
                                    ]
                                },
                            },
                            # ----- NEW: low_quality_v1_3h -----
                            "low_quality_v1_3h": {
                                "is_low": {
                                    "$ifNull": [
                                        "$ml_flags.low_quality_v1_3h.is_low",
                                        False,
                                    ]
                                },
                                "score": {
                                    "$ifNull": [
                                        "$ml_flags.low_quality_v1_3h.score",
                                        0,
                                    ]
                                },
                                "threshold": {
                                    "$ifNull": [
                                        "$ml_flags.low_quality_v1_3h.threshold",
                                        None,
                                    ]
                                },
                                "updated_at": {
                                    "$ifNull": [
                                        "$ml_flags.low_quality_v1_3h.updated_at",
                                        None,
                                    ]
                                },
                            },
                            # ----- low_quality_v3_6h (giữ như cũ, đảm bảo có default) -----
                            "low_quality_v3_6h": {
                                "is_low": {
                                    "$ifNull": [
                                        "$ml_flags.low_quality_v3_6h.is_low",
                                        False,
                                    ]
                                },
                                "score": {
                                    "$ifNull": [
                                        "$ml_flags.low_quality_v3_6h.score",
                                        0,
                                    ]
                                },
                                "threshold": {
                                    "$ifNull": [
                                        "$ml_flags.low_quality_v3_6h.threshold",
                                        None,
                                    ]
                                },
                                "updated_at": {
                                    "$ifNull": [
                                        "$ml_flags.low_quality_v3_6h.updated_at",
                                        None,
                                    ]
                                },
                            },
                        },
                    ]
                }
            }
        },
        {
            "$unset": [
                "ml_flags.likely_viral",
                "ml_flags.viral_confirmed",
                "ml_flags.score",
                "ml_flags.updated_at",
            ]
        },
    ]

    res = coll.update_many(match, pipeline) if match else coll.update_many({}, pipeline)
    return res.matched_count, res.modified_count


def migrate_one(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Per-document updates (fallback mode)."""
    ml = doc.get("ml_flags") or {}

    out_viral = dict(ml.get("viral_v1") or {})
    out_q3h = dict(ml.get("low_quality_v1_3h") or {})
    out_q6h = dict(ml.get("low_quality_v3_6h") or {})

    # map legacy flat → viral_v1 (giữ y chang file cũ)
    if "likely_viral" in ml and "likely" not in out_viral:
        out_viral["likely"] = bool(ml.get("likely_viral"))
    if "viral_confirmed" in ml and "confirmed" not in out_viral:
        out_viral["confirmed"] = bool(ml.get("viral_confirmed"))
    if "score" in ml and "score" not in out_viral:
        out_viral.setdefault("score", ml.get("score") or 0)
    if "updated_at" in ml and "updated_at" not in out_viral:
        out_viral["updated_at"] = ml.get("updated_at")

    # đảm bảo low_quality_v1_3h có đủ field với default
    out_q3h.setdefault("is_low", False)
    out_q3h.setdefault("score", 0)
    out_q3h.setdefault("threshold", None)
    out_q3h.setdefault("updated_at", None)

    # đảm bảo low_quality_v3_6h có đủ field với default
    out_q6h.setdefault("is_low", False)
    out_q6h.setdefault("score", 0)
    out_q6h.setdefault("threshold", None)
    out_q6h.setdefault("updated_at", None)

    set_fields = {
        "ml_flags.viral_v1": out_viral,
        "ml_flags.low_quality_v1_3h": out_q3h,
        "ml_flags.low_quality_v3_6h": out_q6h,
    }
    unset_fields = {
        "ml_flags.likely_viral": "",
        "ml_flags.viral_confirmed": "",
        "ml_flags.score": "",
        "ml_flags.updated_at": "",
    }
    return {"$set": set_fields, "$unset": unset_fields}


def run_fallback_updates(coll, only_legacy: bool, batch_size: int = 500) -> Tuple[int, int]:
    """Per-document updates for servers without update-pipeline."""
    q: Dict[str, Any] = _legacy_query() if only_legacy else {}

    cur = coll.find(q, {"ml_flags": 1})
    matched = 0
    modified_total = 0
    ops = []

    for doc in cur:
        matched += 1
        upd = migrate_one(doc)
        ops.append(UpdateOne({"_id": doc["_id"]}, upd))
        if len(ops) >= batch_size:
            res = coll.bulk_write(ops, ordered=False)
            modified_total += res.modified_count
            ops = []

    if ops:
        res = coll.bulk_write(ops, ordered=False)
        modified_total += res.modified_count

    return matched, modified_total


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Migrate videos.ml_flags to new schema "
            "(viral_v1, low_quality_v1_3h, low_quality_v3_6h)."
        )
    )
    ap.add_argument(
        "--mongo-uri",
        default=os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan"),
        help=(
            "Mongo connection string (may include /db). "
            "Default: env MONGO_URI or ytscan on localhost."
        ),
    )
    ap.add_argument(
        "--db",
        default=os.getenv("MONGO_DB", "ytscan"),
        help="Database name. Default: ytscan",
    )
    ap.add_argument(
        "--coll",
        default=os.getenv("MONGO_COLL", "videos"),
        help="Collection name. Default: videos",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview counts only; do not modify data.",
    )
    ap.add_argument(
        "--only-legacy",
        action="store_true",
        help=(
            "Update only 'legacy' docs: còn flat keys hoặc chưa có "
            "ml_flags.low_quality_v1_3h."
        ),
    )
    args = ap.parse_args()

    client = MongoClient(args.mongo_uri)
    db = client[args.db]
    coll = db[args.coll]

    total, legacy = count_docs(coll, args.only_legacy)
    print(f"[INFO] Collection: {args.db}.{args.coll}")
    print(f"[INFO] Total docs: {total:,} | Legacy-ish docs: {legacy:,}")

    if args.dry_run:
        print("[DRY-RUN] No changes applied.")
        return 0

    info = client.server_info()
    agg_ok = has_agg_update(info)
    print(
        f"[INFO] MongoDB version: {info.get('version')} | "
        f"pipeline updates supported: {agg_ok}"
    )

    if agg_ok:
        matched, modified = run_pipeline_update(coll, args.only_legacy)
    else:
        print("[WARN] Server likely < 4.2 — falling back to per-document bulk updates.")
        matched, modified = run_fallback_updates(coll, args.only_legacy)

    print(f"[DONE] Matched: {matched:,} | Modified: {modified:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
