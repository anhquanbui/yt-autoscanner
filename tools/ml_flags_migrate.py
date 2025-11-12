#!/usr/bin/env python3
# tools/ml_flags_migrate.py
# Reusable CLI to migrate videos.ml_flags from legacy flat keys
# to the new nested schema: viral_v1 {...}, low_quality_v3_6h {...}
#
# - Safe to re-run (idempotent).
# - Supports --dry-run to preview counts.
# - Detects MongoDB server version; uses update-pipeline when available,
#   otherwise falls back to per-document updates.
#
# Usage examples (Windows/Linux/macOS):
#   python -m tools.ml_flags_migrate --mongo-uri mongodb://localhost:27017/ytscan
#   python -m tools.ml_flags_migrate --mongo-uri ... --db ytscan --coll videos
#   python -m tools.ml_flags_migrate --mongo-uri ... --dry-run
#   python -m tools.ml_flags_migrate --only-legacy
#
# Tip: backup first (recommended):
#   mongodump --uri="mongodb://localhost:27017/ytscan" --db ytscan --collection videos -o dump_before_mlflags_migration

from __future__ import annotations
import os, sys, argparse, math
from typing import Any, Dict, Iterable, Tuple

try:
    from pymongo import MongoClient, UpdateOne
except Exception as e:
    print("ERROR: pymongo not installed. pip install pymongo", file=sys.stderr)
    raise

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
        # be optimistic if we can't parse; most modern servers support it
        return True

def count_docs(coll, only_legacy: bool) -> Tuple[int, int]:
    q_legacy = {
        "$or": [
            {"ml_flags.likely_viral": {"$exists": True}},
            {"ml_flags.viral_confirmed": {"$exists": True}},
            {"ml_flags.score": {"$exists": True}},
            {"ml_flags.updated_at": {"$exists": True}},
        ]
    }
    total = coll.estimated_document_count()
    legacy = coll.count_documents(q_legacy)
    if only_legacy:
        return legacy, legacy
    return total, legacy

def run_pipeline_update(coll, only_legacy: bool) -> Tuple[int, int]:
    """Use updateMany with aggregation pipeline; returns (matched, modified)."""
    match = {}
    if only_legacy:
        match = {
            "$or": [
                {"ml_flags.likely_viral": {"$exists": True}},
                {"ml_flags.viral_confirmed": {"$exists": True}},
                {"ml_flags.score": {"$exists": True}},
                {"ml_flags.updated_at": {"$exists": True}},
            ]
        }
    pipeline = [
        {"$set": {
            "ml_flags": {
                "$mergeObjects": [
                    "$ml_flags",
                    {
                        "viral_v1": {
                            "likely": {
                                "$ifNull": [
                                    "$ml_flags.viral_v1.likely",
                                    {"$ifNull": ["$ml_flags.likely_viral", False]}
                                ]
                            },
                            "confirmed": {
                                "$ifNull": [
                                    "$ml_flags.viral_v1.confirmed",
                                    {"$ifNull": ["$ml_flags.viral_confirmed", False]}
                                ]
                            },
                            "score": {
                                "$ifNull": [
                                    "$ml_flags.viral_v1.score",
                                    {"$ifNull": ["$ml_flags.score", 0]}
                                ]
                            },
                            "updated_at": {
                                "$ifNull": [
                                    "$ml_flags.viral_v1.updated_at",
                                    "$ml_flags.updated_at"
                                ]
                            },
                        },
                        "low_quality_v3_6h": {
                            "is_low":     {"$ifNull": ["$ml_flags.low_quality_v3_6h.is_low", False]},
                            "score":      {"$ifNull": ["$ml_flags.low_quality_v3_6h.score", 0]},
                            "threshold":  {"$ifNull": ["$ml_flags.low_quality_v3_6h.threshold", None]},
                            "updated_at": {"$ifNull": ["$ml_flags.low_quality_v3_6h.updated_at", None]},
                        }
                    }
                ]
            }
        }},
        {"$unset": ["ml_flags.likely_viral", "ml_flags.viral_confirmed", "ml_flags.score", "ml_flags.updated_at"]},
    ]
    res = coll.update_many(match, pipeline) if match else coll.update_many({}, pipeline)
    return res.matched_count, res.modified_count

def migrate_one(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Compute $set/$unset for a single document (fallback mode)."""
    ml = doc.get("ml_flags") or {}
    out_viral = dict(ml.get("viral_v1") or {})
    out_lowq  = dict(ml.get("low_quality_v3_6h") or {})

    # map legacy -> new
    if "likely_viral" in ml and "likely" not in out_viral:
        out_viral["likely"] = bool(ml.get("likely_viral"))
    if "viral_confirmed" in ml and "confirmed" not in out_viral:
        out_viral["confirmed"] = bool(ml.get("viral_confirmed"))
    if "score" in ml and "score" not in out_viral:
        # if score already exists under viral_v1, keep it; else use legacy
        out_viral.setdefault("score", ml.get("score") or 0)
    if "updated_at" in ml and "updated_at" not in out_viral:
        out_viral["updated_at"] = ml.get("updated_at")

    # ensure low_quality subtree exists
    out_lowq.setdefault("is_low", False)
    out_lowq.setdefault("score", 0)
    out_lowq.setdefault("threshold", None)
    out_lowq.setdefault("updated_at", None)

    set_fields = {
        "ml_flags.viral_v1": out_viral,
        "ml_flags.low_quality_v3_6h": out_lowq,
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
    q = {}
    if only_legacy:
        q = {
            "$or": [
                {"ml_flags.likely_viral": {"$exists": True}},
                {"ml_flags.viral_confirmed": {"$exists": True}},
                {"ml_flags.score": {"$exists": True}},
                {"ml_flags.updated_at": {"$exists": True}},
            ]
        }
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
    ap = argparse.ArgumentParser(description="Migrate videos.ml_flags to new schema (viral_v1, low_quality_v3_6h).")
    ap.add_argument("--mongo-uri", default=os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan"),
                    help="Mongo connection string (may include /db). Default: env MONGO_URI or ytscan on localhost.")
    ap.add_argument("--db", default=os.getenv("MONGO_DB", "ytscan"), help="Database name. Default: ytscan")
    ap.add_argument("--coll", default=os.getenv("MONGO_COLL", "videos"), help="Collection name. Default: videos")
    ap.add_argument("--dry-run", action="store_true", help="Preview counts only; do not modify data.")
    ap.add_argument("--only-legacy", action="store_true", help="Update only documents that still have legacy keys.")
    args = ap.parse_args()

    client = MongoClient(args.mongo_uri)
    db = client[args.db]
    coll = db[args.coll]

    total, legacy = count_docs(coll, args.only_legacy)
    print(f"[INFO] Collection: {args.db}.{args.coll}")
    print(f"[INFO] Total docs: {total:,} | With legacy keys: {legacy:,}")

    if args.dry_run:
        print("[DRY-RUN] No changes applied.")
        return 0

    info = client.server_info()
    agg_ok = has_agg_update(info)
    print(f"[INFO] MongoDB version: {info.get('version')} | pipeline updates supported: {agg_ok}")

    if agg_ok:
        matched, modified = run_pipeline_update(coll, args.only_legacy)
    else:
        print("[WARN] Server likely < 4.2 — falling back to per-document bulk updates.")
        matched, modified = run_fallback_updates(coll, args.only_legacy)

    print(f"[DONE] Matched: {matched:,} | Modified: {modified:,}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())