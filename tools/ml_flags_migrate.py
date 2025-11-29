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

The script is **idempotent**: you can safely run it multiple times without
breaking already-migrated documents.

Typical usage:

  python -m tools.ml_flags_migrate
  python -m tools.ml_flags_migrate --only-legacy
  python -m tools.ml_flags_migrate --dry-run --only-legacy --verbose
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
    Return True if the server *likely* supports update with aggregation pipeline
    (MongoDB 4.2+).

    We do a simple check on the server's "version" string, e.g. "4.4.18":

      - Split by "." and consider only major/minor.
      - Return True if version >= 4.2.
      - If parsing fails for any reason, we optimistically return True so the
        script tries the pipeline path first.

    This avoids hard-failing on unusual version formats.
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
        # If we cannot parse, assume "true" to try the pipeline path.
        return True


def legacy_query() -> Dict[str, Any]:
    """
    Build a filter that selects "legacy-ish" documents.

    A document is considered legacy-ish if:

      - It still has any of the old flat keys inside ml_flags:
          ml_flags.likely_viral
          ml_flags.viral_confirmed
          ml_flags.score
          ml_flags.updated_at
        OR
      - It does not yet have ml_flags.low_quality_v1_3h

    This filter is intentionally broad so that:

      - Running the migration twice is safe.
      - We cover both pure-legacy documents and intermediate schemas that are
        missing some nested sub-documents.
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
    Return (total_docs, legacy_docs) for the given collection.

    Parameters
    ----------
    coll:
        Target collection (usually `videos`).
    only_legacy:
        If True, the migration will later be applied only to the subset returned
        by `legacy_query()`. Here we still report both total and legacy counts.

    Returns
    -------
    (total_docs, legacy_docs)
        total_docs  : estimated total document count (fast, approximate).
        legacy_docs : exact count of documents matching `legacy_query()`.
    """
    q_legacy = legacy_query()
    total = coll.estimated_document_count()
    legacy = coll.count_documents(q_legacy)
    if only_legacy:
        # When only_legacy is True, we treat "total" as "how many we will touch",
        # which simplifies log messages later.
        return legacy, legacy
    return total, legacy


# =====================================================================
#                 PIPELINE-BASED UPDATE (MongoDB 4.2+)
# =====================================================================

def build_update_pipeline() -> list[Dict[str, Any]]:
    """
    Build an aggregation pipeline used in updateMany (MongoDB 4.2+).

    High-level behavior
    -------------------
    The pipeline:

      - Populates ml_flags.viral_v1 with:
          - likely      ← existing viral_v1.likely
                          or legacy likely_viral
                          or default False
          - confirmed   ← existing viral_v1.confirmed
                          or legacy viral_confirmed
                          or default False
          - score       ← existing viral_v1.score
                          or legacy score
                          or default 0
          - updated_at  ← existing viral_v1.updated_at
                          or legacy updated_at
                          or default null

      - Ensures ml_flags.low_quality_v1_3h has all fields:
          - is_low      (default False)
          - score       (default 0)
          - threshold   (default null)
          - updated_at  (default null)

      - Ensures ml_flags.low_quality_v3_6h has all fields with the same defaults.

    Important
    ---------
    - The logic intentionally **does not** drop or touch other fields under
      `ml_flags`. Only the three sub-documents (viral_v1, low_quality_v1_3h,
      low_quality_v3_6h) are overwritten/repopulated.
    - This makes the migration safe to re-run and compatible with potential
      extra fields you may have added for other models.
    """
    return [
        {
            "$set": {
                # ----------------- viral_v1 -----------------
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

                # ----------------- low_quality_v1_3h -----------------
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

                # ----------------- low_quality_v3_6h -----------------
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
    Use `updateMany(filter, pipeline)` with an aggregation pipeline.

    Parameters
    ----------
    coll:
        Target collection (usually `videos`).
    only_legacy:
        If True, restrict updates to documents matching `legacy_query()`.
        If False, apply the pipeline to all documents in the collection.
    verbose:
        If True, print the filter and pipeline stages for inspection.

    Returns
    -------
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

    Merge order / precedence
    ------------------------
    It merges:

      - legacy flat keys (likely_viral, viral_confirmed, score, updated_at)
      - existing nested structures:
            ml_flags.viral_v1
            ml_flags.low_quality_v1_3h
            ml_flags.low_quality_v3_6h

    The precedence is:

      1. Existing nested sub-doc fields (e.g. ml_flags.viral_v1.likely) are kept.
      2. Legacy flat fields are used only when the nested field is missing.
      3. Default values are filled in for any remaining missing fields.

    Result
    ------
    Returns a document suitable to pass to UpdateOne:

      {
        "$set": {
          "ml_flags.viral_v1": {...},
          "ml_flags.low_quality_v1_3h": {...},
          "ml_flags.low_quality_v3_6h": {...},
        }
      }

    This function does **not** attempt to prune old flat keys; it only
    guarantees that the new nested structure is populated.
    """
    ml = doc.get("ml_flags") or {}

    # Start from existing nested values (if any), so we don't overwrite
    # fields that are already in the target shape.
    out_viral = dict(ml.get("viral_v1") or {})
    out_q3h = dict(ml.get("low_quality_v1_3h") or {})
    out_q6h = dict(ml.get("low_quality_v3_6h") or {})

    # ---- Legacy → viral_v1 ----
    if "likely_viral" in ml and "likely" not in out_viral:
        out_viral["likely"] = bool(ml.get("likely_viral"))
    if "viral_confirmed" in ml and "confirmed" not in out_viral:
        out_viral["confirmed"] = bool(ml.get("viral_confirmed"))
    if "score" in ml and "score" not in out_viral:
        # Use the legacy score if defined, otherwise default to 0.
        out_viral.setdefault("score", ml.get("score") or 0)
    if "updated_at" in ml and "updated_at" not in out_viral:
        out_viral["updated_at"] = ml.get("updated_at")

    # Ensure low_quality_v1_3h fields with defaults (do not remove existing values).
    out_q3h.setdefault("is_low", False)
    out_q3h.setdefault("score", 0)
    out_q3h.setdefault("threshold", None)
    out_q3h.setdefault("updated_at", None)

    # Ensure low_quality_v3_6h fields with defaults.
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

    Strategy
    --------
    - Query documents (legacy-only or all, depending on `only_legacy`).
    - For each document:
        * Compute a `$set` using `migrate_one`.
        * Accumulate UpdateOne operations.
    - Write updates in batches using `bulk_write` for efficiency.

    Parameters
    ----------
    coll:
        Target collection.
    only_legacy:
        If True, restrict to documents matching `legacy_query()`.
    batch_size:
        Maximum number of UpdateOne ops per bulk_write call (default: 1000).
    limit:
        Optional cap on number of documents processed (useful for testing).
    verbose:
        If True, log batch sizes and modified counts.

    Returns
    -------
    (matched_count, modified_count)
        matched_count  : number of documents *seen* in the cursor.
        modified_count : number of documents actually modified by MongoDB.
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

    # Flush any remaining operations in the last batch.
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
    """
    CLI entrypoint for the migration script.

    Steps:
      1. Parse command-line arguments.
      2. Load environment variables via config.env (MONGO_URI, MONGO_DB, MONGO_COLL).
      3. Connect to MongoDB and resolve target DB/collection.
      4. Count total vs legacy-ish documents.
      5. If --dry-run: print stats and exit.
      6. Detect whether the server supports pipeline updates.
      7. Run either:
           - aggregation-based updateMany (preferred, MongoDB 4.2+), or
           - per-document bulk updates (fallback path).
      8. Print final matched/modified counts.

    Exit codes:
      - 0 on success.
      - Any pymongo exceptions will bubble up and cause a non-zero exit.
    """
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
    # Load .env using the shared config.env rules so this behaves the same
    # as the rest of the codebase (local dev, Docker, systemd, etc.).
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

    # Count documents before we do any work so we can log the impact.
    total, legacy = count_docs(coll, args.only_legacy)
    print(f"[INFO] Total docs: {total:,} | Legacy-ish docs: {legacy:,}")
    if args.limit is not None:
        print(f"[INFO] Limit enabled: will process at most {args.limit:,} documents")

    if args.dry_run:
        print("[DRY-RUN] No changes applied.")
        return 0

    # Decide which migration strategy to use.
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
    # Use SystemExit so the shell receives the integer exit status.
    raise SystemExit(main())
