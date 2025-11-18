#!/usr/bin/env python3
"""
tools/ml_flags_migrate.py

One-off migration tool for `videos.ml_flags` to the unified schema:

  - ml_flags.viral_v1 {...}
  - ml_flags.low_quality_v1_3h {...}
  - ml_flags.low_quality_v3_6h {...}

It supports:

  - Mapping from legacy flat keys:
        ml_flags.likely_viral
        ml_flags.viral_confirmed
        ml_flags.score
        ml_flags.updated_at
  - And from the intermediate schema where ml_flags.viral_v1 / low_quality_v3_6h
    already exist, but may be incomplete.

The script is **idempotent**: you can safely run it multiple times.
"""

from __future__ import annotations

import argparse
from typing import Any, Dict, Tuple

from pymongo import MongoClient, UpdateOne

from config.env import load_env, get_env


# =====================================================================
#                         HELPER FUNCTIONS
# =====================================================================

def has_pipeline_update(server_info: Dict[str, Any]) -> bool:
    """
    Return True if the server likely supports update with aggregation pipeline
    (MongoDB 4.2+).

    We simply check the reported "version" string, e.g. "4.4.18".
    """
    try:
        v = server_info.get("version", "0.0.0")
        parts = [int(x) for x in str(v).split(".")[:2]]
        if len(parts) == 1:
            major, minor = parts[0], 0
        else:
            major, minor = parts
        return (major > 4) or (major == 4 and minor >= 2)
    except Exception:
        # If we cannot parse, assume "true" to try pipeline path.
        return True


def legacy_query() -> Dict[str, Any]:
    """
    A document is considered "legacy-ish" if:

      - It still has any of the flat keys inside ml_flags, OR
      - It does not have ml_flags.low_quality_v1_3h (intermediate schema).

    This is intentionally broad so that running the migration twice is safe.
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
    """
    Return (total_docs, legacy_docs).

    If only_legacy=True, we will only process those legacy docs later,
    but here we still return both numbers for logging.
    """
    q_legacy = legacy_query()
    total = coll.estimated_document_count()
    legacy = coll.count_documents(q_legacy)
    if only_legacy:
        return legacy, legacy
    return total, legacy


# =====================================================================
#                 PIPELINE-BASED UPDATE (MongoDB 4.2+)
# =====================================================================

def build_update_pipeline() -> list[Dict[str, Any]]:
    """
    Build an aggregation pipeline used in updateMany (MongoDB 4.2+).

    The pipeline:

      - Populates ml_flags.viral_v1 with:
          - likely      ← viral_v1.likely or legacy likely_viral or False
          - confirmed   ← viral_v1.confirmed or legacy viral_confirmed or False
          - score       ← viral_v1.score or legacy score or 0
          - updated_at  ← viral_v1.updated_at or legacy updated_at or null

      - Ensures ml_flags.low_quality_v1_3h has all fields:
          - is_low      (default False)
          - score       (default 0)
          - threshold   (default null)
          - updated_at  (default null)

      - Ensures ml_flags.low_quality_v3_6h has all fields with the same defaults.

    The logic intentionally **does not** drop existing extra fields in ml_flags;
    it only overwrites the three sub-documents mentioned above.
    """
    return [
        {
            "$set": {
                "ml_flags.viral_v1": {
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
                "ml_flags.low_quality_v1_3h": {
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
                "ml_flags.low_quality_v3_6h": {
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
            }
        }
    ]


def run_pipeline_update(coll, only_legacy: bool, verbose: bool = False) -> Tuple[int, int]:
    """
    Use `updateMany(filter, pipeline)` with aggregation pipeline.

    Returns:
      (matched_count, modified_count)
    """
    match: Dict[str, Any] = legacy_query() if only_legacy else {}

    if verbose:
        print("[DEBUG] Pipeline filter:")
        print(match)
        print("[DEBUG] Pipeline stages:")
        for stage in build_update_pipeline():
            print(stage)

    res = coll.update_many(match, build_update_pipeline())
    return res.matched_count, res.modified_count


# =====================================================================
#                 FALLBACK PER-DOCUMENT BULK UPDATE
# =====================================================================

def migrate_one(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute the `$set` document for a single Mongo document.

    It merges:

      - legacy flat keys (likely_viral, viral_confirmed, score, updated_at)
      - existing ml_flags.viral_v1 / low_quality_v1_3h / low_quality_v3_6h

    and returns something like:

      {
        "$set": {
          "ml_flags.viral_v1": {...},
          "ml_flags.low_quality_v1_3h": {...},
          "ml_flags.low_quality_v3_6h": {...},
        }
      }
    """
    ml = doc.get("ml_flags") or {}
    # Start from existing nested values (if any)
    out_viral = dict(ml.get("viral_v1") or {})
    out_q3h = dict(ml.get("low_quality_v1_3h") or {})
    out_q6h = dict(ml.get("low_quality_v3_6h") or {})

    # ---- Legacy → viral_v1 ----
    if "likely_viral" in ml and "likely" not in out_viral:
        out_viral["likely"] = bool(ml.get("likely_viral"))
    if "viral_confirmed" in ml and "confirmed" not in out_viral:
        out_viral["confirmed"] = bool(ml.get("viral_confirmed"))
    if "score" in ml and "score" not in out_viral:
        out_viral.setdefault("score", ml.get("score") or 0)
    if "updated_at" in ml and "updated_at" not in out_viral:
        out_viral["updated_at"] = ml.get("updated_at")

    # Ensure low_quality_v1_3h fields with defaults
    out_q3h.setdefault("is_low", False)
    out_q3h.setdefault("score", 0)
    out_q3h.setdefault("threshold", None)
    out_q3h.setdefault("updated_at", None)

    # Ensure low_quality_v3_6h fields with defaults
    out_q6h.setdefault("is_low", False)
    out_q6h.setdefault("score", 0)
    out_q6h.setdefault("threshold", None)
    out_q6h.setdefault("updated_at", None)

    set_fields = {
        "ml_flags.viral_v1": out_viral,
        "ml_flags.low_quality_v1_3h": out_q3h,
        "ml_flags.low_quality_v3_6h": out_q6h,
    }
    return {"$set": set_fields}


def run_fallback_updates(
    coll,
    only_legacy: bool,
    batch_size: int = 1000,
    limit: int | None = None,
    verbose: bool = False,
) -> Tuple[int, int]:
    """
    Fallback path for servers that do not support pipeline updates.

    It iterates over legacy docs (or all docs, depending on flags),
    computes per-doc `$set` with `migrate_one`, and writes them in bulk.

    Returns:
      (matched_count, modified_count)
    """
    match: Dict[str, Any] = legacy_query() if only_legacy else {}

    cur = coll.find(match, projection={"_id": 1, "ml_flags": 1})
    if limit is not None:
        cur = cur.limit(int(limit))

    matched = 0
    modified_total = 0
    ops: list[UpdateOne] = []

    for doc in cur:
        matched += 1
        upd = migrate_one(doc)
        ops.append(UpdateOne({"_id": doc["_id"]}, upd))

        if len(ops) >= batch_size:
            res = coll.bulk_write(ops, ordered=False)
            modified_total += res.modified_count
            if verbose:
                print(f"[DEBUG] Bulk write: matched+={len(ops)}, modified+={res.modified_count}")
            ops = []

    if ops:
        res = coll.bulk_write(ops, ordered=False)
        modified_total += res.modified_count
        if verbose:
            print(f"[DEBUG] Final bulk write: matched+={len(ops)}, modified+={res.modified_count}")

    return matched, modified_total


# =====================================================================
#                               CLI
# =====================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Migrate videos.ml_flags to new schema "
            "(viral_v1, low_quality_v1_3h, low_quality_v3_6h)."
        )
    )

    # Let argparse defaults be None; we resolve env / fallback inside.
    ap.add_argument(
        "--mongo-uri",
        dest="mongo_uri",
        default=None,
        help=(
            "Mongo connection string (may include /db). "
            "Default: env MONGO_URI or mongodb://localhost:27017/ytscan"
        ),
    )
    ap.add_argument(
        "--db",
        dest="db",
        default=None,
        help="Database name. Default: env MONGO_DB or 'ytscan'.",
    )
    ap.add_argument(
        "--coll",
        dest="coll",
        default=None,
        help="Collection name. Default: env MONGO_COLL or 'videos'.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print statistics; do not modify any data.",
    )
    ap.add_argument(
        "--only-legacy",
        action="store_true",
        help=(
            "Update only 'legacy-ish' docs: those that still have flat keys "
            "or do not yet have ml_flags.low_quality_v1_3h."
        ),
    )
    ap.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Per-bulk batch size for fallback mode. Default: 1000.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit for number of documents processed (debug/testing).",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Print extra debug information.",
    )

    args = ap.parse_args()

    # --- Unified env loading ---
    load_env()

    mongo_uri = (
        args.mongo_uri
        or get_env("MONGO_URI")
        or "mongodb://localhost:27017/ytscan"
    )
    db_name = args.db or get_env("MONGO_DB") or "ytscan"
    coll_name = args.coll or get_env("MONGO_COLL") or "videos"

    print(f"[INFO] Mongo URI: {mongo_uri}")
    print(f"[INFO] Target collection: {db_name}.{coll_name}")

    client = MongoClient(mongo_uri)
    db = client[db_name]
    coll = db[coll_name]

    total, legacy = count_docs(coll, args.only_legacy)
    print(f"[INFO] Total docs: {total:,} | Legacy-ish docs: {legacy:,}")
    if args.limit is not None:
        print(f"[INFO] Limit enabled: will process at most {args.limit:,} documents")

    if args.dry_run:
        print("[DRY-RUN] No changes applied.")
        return 0

    info = client.server_info()
    support_pipeline = has_pipeline_update(info)
    print(
        f"[INFO] MongoDB version: {info.get('version')} | "
        f"pipeline updates supported: {support_pipeline}"
    )

    if support_pipeline:
        print("[INFO] Using aggregation pipeline updateMany()")
        matched, modified = run_pipeline_update(coll, args.only_legacy, verbose=args.verbose)
    else:
        print("[WARN] Server likely < 4.2 — falling back to per-document bulk updates.")
        matched, modified = run_fallback_updates(
            coll,
            args.only_legacy,
            batch_size=args.batch_size,
            limit=args.limit,
            verbose=args.verbose,
        )

    print(f"[DONE] Matched: {matched:,} | Modified: {modified:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
