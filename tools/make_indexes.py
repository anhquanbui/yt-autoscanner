#!/usr/bin/env python3
"""
make_indexes.py — Smart MongoDB Index Manager (v6, tuned for new pipeline)
-------------------------------------------------------------------------------
- Based on your v5 script, updated to:
    • Use central config.env loader (load_env / get_env).
    • Avoid reading random system-wide .env (e.g. C:\\Users\\Admin\\.env).
    • Clean up duplicate index specs (same keys, different names).
    • Keep optimized indexes for:
        - tracking queue (track_once)
        - low_quality 3h & 6h workers
        - viral_v2 6h / 12h / 24h + final
        - processed_videos analytics (horizons, snapshot_features)
- Idempotent; supports --show-only and --drop-old; collection filtering.

Usage:
  python -m tools.make_indexes                      # create/verify all
  python -m tools.make_indexes --show-only          # dry run (no changes)
  python -m tools.make_indexes --drop-old           # drop indexes not in INDEX_MAP
  python -m tools.make_indexes --collections videos,processed_videos
  python -m tools.make_indexes --mongo-uri ... --db ytscan
"""

from __future__ import annotations

import argparse
import logging
from typing import Dict, List, Tuple

from pymongo import MongoClient
from pymongo.errors import OperationFailure

from config.env import load_env, get_env


# ---------------- Logging (console only) ----------------
# We use a very simple logging configuration:
# - INFO level by default (enough to see what happens).
# - Single-line messages, suitable for systemd / CLI.
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)


# ---------------- CLI ----------------
def parse_args() -> argparse.Namespace:
    """
    Parse CLI arguments and resolve environment-driven defaults.

    Env resolution rules:
      - MONGO_URI: default Mongo connection string.
      - MONGO_DB : optional DB override; if missing, DB is inferred from URI tail.

    This function also ensures that .env files are loaded before reading
    any MONGO_* variables, using the config.env loader (shared across the project).
    """
    # Ensure .env is loaded according to config.env priority:
    # 1) ~/.env
    # 2) <project_root>/.env
    # 3) Nested .env files under the project tree.
    load_env()

    p = argparse.ArgumentParser(description="Smart MongoDB index manager (v6).")

    p.add_argument(
        "--mongo-uri",
        default=get_env("MONGO_URI", "mongodb://127.0.0.1:27017/ytscan"),
        help=(
            "MongoDB URI (default: from MONGO_URI or "
            "mongodb://127.0.0.1:27017/ytscan)"
        ),
    )
    p.add_argument(
        "--db",
        default=get_env("MONGO_DB"),
        help=(
            "DB name. Default: from MONGO_DB, or inferred from URI tail "
            "(e.g. mongodb://host:27017/ytscan → ytscan)."
        ),
    )
    p.add_argument(
        "--show-only",
        action="store_true",
        help="Show what would be done, but make no changes.",
    )
    p.add_argument(
        "--drop-old",
        action="store_true",
        help="Drop indexes that are not in INDEX_MAP (by key signature).",
    )
    p.add_argument(
        "--rebuild",
        action="store_true",
        help=(
            "Drop all non-_id_ indexes on selected collections, then recreate "
            "all indexes defined in INDEX_MAP."
        ),
    )
    p.add_argument(
        "--collections",
        type=str,
        default="all",
        help="Comma-separated list of collections (default: all from INDEX_MAP).",
    )

    return p.parse_args()


# ---------------- Mongo helpers ----------------
def _infer_db_name_from_uri(uri: str, fallback: str = "ytscan") -> str:
    """
    Infer DB name from a Mongo URI, e.g.:

      mongodb://host:27017/ytscan           -> ytscan
      mongodb://host:27017/ytscan?retry=1   -> ytscan
      mongodb://host:27017                  -> fallback

    This is only used when the user does not provide --db or MONGO_DB.
    """
    tail = uri.rsplit("/", 1)[-1]
    db_part = tail.split("?", 1)[0]
    return db_part or fallback


def connect_db(mongo_uri: str, explicit_db: str | None):
    """
    Open a MongoClient and return (client, db).

    DB name is chosen by:
      - explicit_db (from --db or MONGO_DB) if provided,
      - otherwise inferred from the URI tail (with a safe fallback).
    """
    client = MongoClient(mongo_uri)
    if explicit_db:
        db = client[explicit_db]
    else:
        db_name = _infer_db_name_from_uri(mongo_uri, fallback="ytscan")
        db = client[db_name]
    return client, db


# ---------------- Index Map ----------------
# Central definition of all indexes for each collection.
#
# The structure is:
#   INDEX_MAP: Dict[str, List[dict]]
# where each dict describes a single index:
#   {
#       "keys":   [("field", 1 or -1), ...],
#       "name":   "optional_name",
#       "unique": bool,
#       "partial": dict (partialFilterExpression),
#   }
#
# Notes:
# - "keys" must be ordered; MongoDB index ordering matters.
# - "partial" is translated to partialFilterExpression automatically.
# - We intentionally identify indexes by their "keys" signature when deciding
#   whether an index already exists, so that renaming an index in code does not
#   force a rebuild.
INDEX_MAP: Dict[str, List[dict]] = {
    # === SOURCE COLLECTIONS ===
    "videos": [
        # ------------------------------------------------------------------
        # Tracking queues (track_once)
        # ------------------------------------------------------------------
        # Optimized queue index:
        #   - (tracking.status, tracking.next_poll_after)
        #   - partial filter on statuses that represent "active" queue states.
        {
            "keys": [
                ("tracking.status", 1),
                ("tracking.next_poll_after", 1),
            ],
            "name": "trackStatus_nextPoll_active",
            "partial": {
                "tracking.status": {"$in": ["queued", "tracking", "retry"]}
            },
        },
        # Used for filtering by status and ordering by recency of publish.
        {
            "keys": [
                ("tracking.status", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "trackStatus_publishedAt_desc",
        },
        # Channel-level queries: fetch videos for a channel ordered by publish time.
        {
            "keys": [
                ("snippet.channelId", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "channelId_publishedAt_desc",
        },
        # Region filter + publish date (used in discover / analytics).
        {
            "keys": [
                ("source.regionCode", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "region_publishedAt_desc",
        },
        # Query seed + publish date (used for search query-based discover).
        {
            "keys": [
                ("source.query", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "query_publishedAt_desc",
            "partial": {
                "source.query": {"$exists": True, "$type": "string"},
            },
        },
        # Category / length bucket filters for slicing traffic by content type.
        {
            "keys": [
                ("snippet.categoryId", 1),
                ("snippet.lengthBucket", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "category_lengthBucket_publishedAt_desc",
        },
        # Basic global recency index (most recent videos first).
        {
            "keys": [
                ("snippet.publishedAt", -1),
            ],
            "name": "publishedAt_desc",
        },

        # ------------------------------------------------------------------
        # Shared base for low-quality workers (3h & 6h)
        # ------------------------------------------------------------------
        # Both 3h and 6h pipelines always require:
        #   - stats_snapshots.0 exists (early snapshot)
        #   - often filter by tracking.status
        {
            "keys": [
                ("stats_snapshots.0", 1),
            ],
            "name": "snap0_exists",
        },
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("tracking.status", 1),
            ],
            "name": "snap0_trackingStatus",
        },

        # ------------------------------------------------------------------
        # low_quality_v3_6h ML pipeline
        # ------------------------------------------------------------------
        # Used for:
        #   - build_query(..., mode="6h-only"/"both", only_missing=True/False)
        #   - filtering by is_low + publishedAt (analytics/dashboard).
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.low_quality_v3_6h.updated_at", 1),
            ],
            "name": "lowq6h_snap0_updatedAt",
        },
        {
            "keys": [
                ("ml_flags.low_quality_v3_6h.is_low", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "lowq6h_isLow_publishedAt_desc",
        },
        # Compound index tuned for the 6h worker to match the core query shape:
        #   {
        #     stats_snapshots.0: { $exists: true },
        #     tracking.status: "tracking",
        #     ml_flags.low_quality_v3_6h.updated_at: { $exists: false }  (when only_missing)
        #   }
        {
            "keys": [
                ("tracking.status", 1),
                ("stats_snapshots.0", 1),
                ("ml_flags.low_quality_v3_6h.updated_at", 1),
            ],
            "name": "lowq6h_track_snap0_updatedAt_tracking",
            "partial": {
                "tracking.status": "tracking",
                "stats_snapshots.0": {"$exists": True},
            },
        },

        # ------------------------------------------------------------------
        # low_quality_v1_3h ML pipeline
        # ------------------------------------------------------------------
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.low_quality_v1_3h.updated_at", 1),
            ],
            "name": "lowq3h_snap0_updatedAt",
        },
        {
            "keys": [
                ("ml_flags.low_quality_v1_3h.is_low", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "lowq3h_isLow_publishedAt_desc",
        },
        # Compound index tuned for the 3h worker, matching build_query(... mode="3h-only"/"both").
        {
            "keys": [
                ("tracking.status", 1),
                ("stats_snapshots.0", 1),
                ("ml_flags.low_quality_v1_3h.updated_at", 1),
            ],
            "name": "lowq3h_track_snap0_updatedAt_tracking",
            "partial": {
                "tracking.status": "tracking",
                "stats_snapshots.0": {"$exists": True},
            },
        },

        # ====================================================
        # Viral_v2 ML pipeline (6h / 12h / 24h + final)
        # ====================================================

        # Match query of viral_prediction_core for 6h stage:
        #   { stats_snapshots.0: { $exists: true },
        #     ml_flags.viral_v2.h6.score_proba: null }  (when --only-missing)
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.viral_v2.h6.score_proba", 1),
            ],
            "name": "viral_h6_snap0_score",
        },

        # 12h stage:
        #   { stats_snapshots.0: { $exists: true },
        #     ml_flags.viral_v2.h12.score_proba: null }
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.viral_v2.h12.score_proba", 1),
            ],
            "name": "viral_h12_snap0_score",
        },

        # 24h validation stage:
        #   { stats_snapshots.0: { $exists: true },
        #     ml_flags.viral_v2.h24_validation.score_proba: null }
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.viral_v2.h24_validation.score_proba", 1),
            ],
            "name": "viral_h24_snap0_score",
        },

        # Dashboard / analytics: filter by final.status and sort by publish time.
        {
            "keys": [
                ("ml_flags.viral_v2.final.status", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "viral_finalStatus_publishedAt_desc",
        },

        # Optional: quickly sort by latest_stats_ts for age-based filters / finalize.
        {
            "keys": [
                ("latest_stats_ts", -1),
            ],
            "name": "latestStatsTs_desc",
        },
    ],

    # === OUTPUT COLLECTIONS ===
    # process_data writes to "processed_videos"
    "processed_videos": [
        # Unique logical key per video.
        {
            "keys": [("video_id", 1)],
            "name": "uniq_video_id",
            "unique": True,
        },

        # Status + recency filters (e.g. active vs complete).
        # Often used for dashboards or re-processing queries.
        {
            "keys": [("status", 1), ("last_snapshot_ts", -1)],
            "name": "status_lastSnapshot_desc",
            "partial": {"status": {"$in": ["complete", "tracking"]}},
        },

        # Processed status & processed_at for ETL logs and consistency checks.
        {
            "keys": [("processed_status", 1), ("processed_at", -1)],
            "name": "processedStatus_processedAt_desc",
        },

        # Time ordering by published_at for analytics.
        {
            "keys": [("published_at", -1)],
            "name": "proc_publishedAt_desc",
        },

        # Region / query / duration filters
        {
            "keys": [("source_meta.region_code", 1), ("published_at", -1)],
            "name": "regionCode_published_desc",
            "partial": {"source_meta.region_code": {"$exists": True}},
        },
        {
            "keys": [("source_meta.query_seed", 1), ("published_at", -1)],
            "name": "querySeed_published_desc",
            "partial": {
                "source_meta.query_seed": {
                    "$exists": True,
                    "$type": "string",
                }
            },
        },
        {
            "keys": [("source_meta.duration_bucket", 1), ("published_at", -1)],
            "name": "durationBucket_published_desc",
            "partial": {
                "source_meta.duration_bucket": {
                    "$exists": True,
                    "$type": "string",
                }
            },
        },

        # Growth analysis:
        # - growth_phase: stage of the video growth curve.
        # - coverage_score: how complete the time-series is.
        # - last_snapshot_ts: recency of the last horizon snapshot.
        {
            "keys": [
                ("growth_phase", 1),
                ("coverage_score", -1),
                ("last_snapshot_ts", -1),
            ],
            "name": "growthPhase_coverage_lastSnap_desc",
        },

        # Wildcard indexes for horizons & snapshot_features:
        # These make ad-hoc queries and analytics on nested fields much faster,
        # at the cost of a bigger index. This is acceptable for analytics-heavy usage.
        {
            "keys": [("horizons.$**", 1)],
            "name": "wild_horizons",
        },
        {
            "keys": [("snapshot_features.$**", 1)],
            "name": "wild_snapshotFeatures",
        },
    ],

    # === CHANNELS COLLECTION ===
    "channels": [
        # Handle-based lookup (unique per channel).
        {
            "keys": [("handle", 1)],
            "name": "handle_uniq",
            "unique": True,
            "partial": {"handle": {"$exists": True, "$type": "string"}},
        },
        # Simple recency index for channels, if you track last_updated yourself.
        {
            "keys": [("last_updated", -1)],
            "name": "lastUpdated_desc",
        },
    ],
}


# ---------------- Index utilities ----------------
def _index_signature(ixdoc) -> Tuple[Tuple[str, int], ...]:
    """
    Compute a stable signature for an index based on its key ordering.

    This is used to:
      - detect whether an equivalent index already exists, regardless of name;
      - decide which indexes to drop when using --drop-old.

    Note: we only consider (field, direction) pairs and ignore options such as unique,
    partialFilterExpression, etc. This is intentional: we aim to avoid creating
    multiple indexes on the same key combination.
    """
    return tuple(ixdoc["key"].items())


def _existing_indexes(coll):
    """
    List all existing index documents for a given collection.

    Wrapper around coll.list_indexes() mainly for clarity and testability.
    """
    return list(coll.list_indexes())


def create_or_verify_collection_indexes(
    db,
    coll_name: str,
    specs: List[dict],
    show_only: bool = False,
) -> Tuple[int, int]:
    """
    Ensure that all indexes from `specs` exist on collection `coll_name`.

    Behavior:
    - If an index with the same key signature already exists, it is skipped
      (we do not care if the names differ).
    - If an index with the same name but different keys exists, we log a
      warning and skip creation to avoid conflicts.
    - If show_only=True, no changes are made; we only print what we would do.

    Returns:
      (created_count, skipped_count)
    """
    coll = db[coll_name]
    existing = _existing_indexes(coll)

    # Map by key signature and by name to avoid collisions and duplicates.
    existing_by_keys = {_index_signature(ix): ix for ix in existing}
    existing_by_name = {ix["name"]: ix for ix in existing}

    logging.info(f"\n📂 Collection: {coll_name}")
    created = skipped = 0

    for spec in specs:
        keys = spec["keys"]
        name = spec.get("name")
        unique = spec.get("unique", False)
        partial = spec.get("partial")
        key_tuple = tuple(keys)

        # 1) If there is already an index with the same keys → skip (whatever its name is).
        if key_tuple in existing_by_keys:
            ix = existing_by_keys[key_tuple]
            logging.info(
                f"   ⏭️  Skipped existing index with same keys: {keys} "
                f"(existing name={ix.get('name')})"
            )
            skipped += 1
            continue

        # 2) If there is an existing index with the same name but different keys → warn & skip.
        if name and name in existing_by_name:
            ix = existing_by_name[name]
            if _index_signature(ix) != key_tuple:
                logging.warning(
                    f"   ⚠️ Existing index with name '{name}' has different keys "
                    f"{list(ix['key'].items())}, skipping creation of {keys}."
                )
                skipped += 1
                continue

        if show_only:
            # Dry-run: only print what we would create.
            logging.info(
                f"   👀 Would create index: {keys}"
                + (f" [name={name}]" if name else "")
                + (" [unique]" if unique else "")
                + (f" [partial={partial}]" if partial else "")
            )
            continue

        # Real creation path.
        # Background index builds are generally safer for production.
        opts = {"background": True}
        if name:
            opts["name"] = name
        if unique:
            opts["unique"] = True
        if partial:
            # Translate our shorthand "partial" spec into Mongo's field name.
            opts["partialFilterExpression"] = partial

        try:
            coll.create_index(keys, **opts)
            logging.info(
                f"   ✅ Created index: {keys}"
                + (f" [name={name}]" if name else "")
                + (" [unique]" if unique else "")
                + (f" [partial]" if partial else "")
            )
            created += 1
        except OperationFailure as e:
            # code 85: IndexOptionsConflict (same key/name but different options).
            if getattr(e, "code", None) == 85:
                logging.warning(
                    f"   ⚠️ Index conflict for {name or keys}: {e}. Skipping."
                )
                skipped += 1
            else:
                # For other errors, bubble up so the caller can see the failure.
                raise

    return created, skipped


def drop_unused_indexes(db, coll_name: str, keep_specs: List[dict]) -> None:
    """
    Drop indexes on `coll_name` that are not listed in `keep_specs`.

    Comparison is based on the key signature (field + direction), not the name.

    This is useful for cleaning up old / experimental indexes that are no longer
    defined in INDEX_MAP, which can otherwise slow down writes and consume disk.
    """
    coll = db[coll_name]
    existing = _existing_indexes(coll)
    keep_signatures = {tuple(s["keys"]) for s in keep_specs}

    for ix in existing:
        if ix["name"] == "_id_":
            # Never drop the default primary index.
            continue
        if _index_signature(ix) not in keep_signatures:
            coll.drop_index(ix["name"])
            logging.info(f"   🗑️  Dropped old index: {ix['name']}")


def drop_all_indexes(db, coll_name: str, show_only: bool = False) -> None:
    """
    Drop all indexes on a collection except the default `_id_` index.

    Intended for use with --rebuild:
    - First drop everything (except _id_).
    - Then recreate all indexes defined in INDEX_MAP from scratch.
    """
    coll = db[coll_name]
    existing = _existing_indexes(coll)

    for ix in existing:
        name = ix["name"]
        if name == "_id_":
            # Always keep the primary key index.
            continue

        if show_only:
            logging.info(f"   👀 Would drop index: {name}")
        else:
            coll.drop_index(name)
            logging.info(f"   🗑️  Dropped index: {name}")


# ---------------- Main entrypoint ----------------
def main() -> int:
    """
    Main entrypoint for the index manager.

    High-level flow:
      1. Parse CLI arguments and connect to MongoDB.
      2. Resolve which collections should be processed (based on INDEX_MAP or CLI).
      3. For each collection:
         - Optional: drop all existing indexes (if --rebuild).
         - Create/verify indexes from INDEX_MAP (respecting --show-only).
         - Optional: drop indexes not defined in INDEX_MAP (if --drop-old).
      4. Print a small summary and exit with status code 0.
    """
    args = parse_args()
    client, db = connect_db(args.mongo_uri, args.db)

    if args.rebuild and args.drop_old:
        # Both flags imply index deletion, but rebuild is a stronger operation.
        logging.warning(
            "⚠️ Both --rebuild and --drop-old were specified. "
            "--rebuild takes precedence for dropping indexes."
        )

    # Determine which collections to process.
    collections = (
        list(INDEX_MAP.keys())
        if args.collections.lower() == "all"
        else [c.strip() for c in args.collections.split(",")]
    )

    total_created = total_skipped = 0
    logging.info("🚀 Starting MongoDB index maintenance...\n")
    logging.info(f"Using DB: {db.name} (uri={args.mongo_uri})")

    for coll_name in collections:
        if coll_name not in INDEX_MAP:
            logging.warning(f"⚠️ Unknown collection '{coll_name}' (skipped).")
            continue

        specs = INDEX_MAP[coll_name]

        # If --rebuild: drop all non-_id_ indexes first, then recreate.
        if args.rebuild:
            drop_all_indexes(db, coll_name, show_only=args.show_only)

        # Create / verify indexes from INDEX_MAP.
        created, skipped = create_or_verify_collection_indexes(
            db,
            coll_name,
            specs,
            show_only=args.show_only,
        )
        total_created += created
        total_skipped += skipped

        # If not rebuilding but --drop-old: remove indexes not in INDEX_MAP.
        # (We only do real drops when not in show-only mode.)
        if args.drop_old and not args.rebuild and not args.show_only:
            drop_unused_indexes(db, coll_name, specs)

    logging.info("\n✅ Index maintenance complete.")
    logging.info(f"   Total created: {total_created}")
    logging.info(f"   Total skipped: {total_skipped}\n")

    return 0


if __name__ == "__main__":
    # Use SystemExit so the shell/systemd receives the integer status code.
    raise SystemExit(main())
