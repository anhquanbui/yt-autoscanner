#!/usr/bin/env python3
"""
make_indexes_v5.py — Smart MongoDB Index Manager (v5, tuned for new pipeline)
-------------------------------------------------------------------------------
- Based on your v4 script, updated for:
    • New low_quality_v3_6h ML flags on `videos`.
    • Current tracking queue pattern (track_once).
    • New processed_videos schema (process_data output, horizons, snapshot_features).
- Adds indexes to speed up low_quality_autoflag (stats_snapshots + ml_flags).
- Keeps wildcard indexes for horizons & snapshot_features to support analytics.
- Idempotent; supports --show-only and --drop-old; collection filtering.

Usage:
  python make_indexes_v5.py                              # create/verify all
  python make_indexes_v5.py --show-only                  # dry run (no changes)
  python make_indexes_v5.py --drop-old                   # drop indexes not in INDEX_MAP
  python make_indexes_v5.py --rebuild                    # drop all and recreate from INDEX_MAP
  python make_indexes_v5.py --collections videos,processed_videos
  python make_indexes_v5.py --mongo-uri ... --db ytscan  # override env/URI db
"""

import os
import argparse
import logging
from typing import Dict, List, Tuple

from pathlib import Path
from dotenv import load_dotenv

from pymongo import MongoClient
from pymongo.errors import OperationFailure


# ----- load .ENV file -----

import config.path_utils

# ---------------- Logging (Console Only) ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)


# ---------------- CLI ----------------
def parse_args():
    p = argparse.ArgumentParser(description="Smart MongoDB index manager (v5).")
    p.add_argument(
        "--mongo-uri",
        default=os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan"),
        help="MongoDB URI (default from MONGO_URI or mongodb://localhost:27017/ytscan)",
    )
    p.add_argument(
        "--db",
        default=None,
        help="DB name (if omitted, taken from URI)",
    )
    p.add_argument(
        "--show-only",
        action="store_true",
        help="Show what would be done, but make no changes.",
    )
    p.add_argument(
        "--drop-old",
        action="store_true",
        help="Drop indexes not in the official INDEX_MAP.",
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


# ---------------- Mongo ----------------
def get_db(mongo_uri: str, explicit_db: str | None):
    client = MongoClient(mongo_uri)
    db = client.get_database()
    if explicit_db:
        db = client[explicit_db]
    return client, db


# ---------------- Index Map ----------------
# Each index spec:
# {"keys": [("field", 1 or -1), ...],
#  "name": "optional_name",
#  "unique": bool,
#  "partial": dict (partialFilterExpression)}
INDEX_MAP: Dict[str, List[dict]] = {
    # === SOURCE COLLECTIONS ===
    "videos": [
        # --- Tracking queues (track_once) ---
        {"keys": [("tracking.status", 1), ("tracking.next_poll_after", 1)],
         "name": "trackStatus_nextPoll"},

        {"keys": [("tracking.status", 1), ("tracking.next_poll_after", 1)],
         "name": "trackStatus_nextPoll_activeOnly",
         "partial": {"tracking.status": {"$in": ["queued", "tracking", "retry"]}}},

        {"keys": [("tracking.status", 1), ("snippet.publishedAt", -1)],
         "name": "trackStatus_publishedAt_desc"},

        {"keys": [("snippet.channelId", 1), ("snippet.publishedAt", -1)],
         "name": "channelId_publishedAt_desc"},

        {"keys": [("source.regionCode", 1), ("snippet.publishedAt", -1)],
         "name": "region_publishedAt_desc"},

        {"keys": [("source.query", 1), ("snippet.publishedAt", -1)],
         "name": "query_publishedAt_desc",
         "partial": {"source.query": {"$exists": True, "$type": "string"}}},

        {"keys": [("snippet.categoryId", 1),
                  ("snippet.lengthBucket", 1),
                  ("snippet.publishedAt", -1)],
         "name": "category_lengthBucket_publishedAt_desc"},

        {"keys": [("snippet.publishedAt", -1)],
         "name": "publishedAt_desc"},

        # --- LOW_QUALITY_V3_6H ML PIPELINE ---
        {"keys": [("stats_snapshots.0", 1)],
         "name": "lowq_snap0_exists"},

        {"keys": [("stats_snapshots.0", 1), ("tracking.status", 1)],
         "name": "lowq_snap0_trackingStatus"},

        {"keys": [("stats_snapshots.0", 1), ("ml_flags.low_quality_v3_6h.updated_at", 1)],
         "name": "lowq_snap0_updatedAt"},

        {"keys": [("ml_flags.low_quality_v3_6h.is_low", 1), ("snippet.publishedAt", -1)],
         "name": "lowq_isLow_publishedAt_desc"},

        # --- NEW: LOW_QUALITY_V1_3H ML PIPELINE ---
        {"keys": [("stats_snapshots.0", 1)],
         "name": "lowq3h_snap0_exists"},

        {"keys": [("stats_snapshots.0", 1), ("tracking.status", 1)],
         "name": "lowq3h_snap0_trackingStatus"},

        {"keys": [("stats_snapshots.0", 1), ("ml_flags.low_quality_v1_3h.updated_at", 1)],
         "name": "lowq3h_snap0_updatedAt"},

        {"keys": [("ml_flags.low_quality_v1_3h.is_low", 1), ("snippet.publishedAt", -1)],
         "name": "lowq3h_isLow_publishedAt_desc"},
    ],

    # === OUTPUT COLLECTIONS ===
    # process_data writes to "processed_videos"
    "processed_videos": [
        # 1-1 mapping by video_id
        {"keys": [("video_id", 1)],
         "name": "uniq_video_id",
         "unique": True},

        # Status + recency filters (e.g. active vs complete)
        {"keys": [("status", 1), ("last_snapshot_ts", -1)],
         "name": "status_lastSnapshot_desc",
         "partial": {"status": {"$in": ["complete", "tracking"]}}},

        # Processed status timeline (for process_data, training/export)
        {"keys": [("processed_status", 1), ("processed_at", -1)],
         "name": "processedStatus_processedAt_desc"},

        # Published_at for global time scans/sorts
        {"keys": [("published_at", -1)],
         "name": "proc_publishedAt_desc"},

        # Source slicing for analytics
        {"keys": [("source_meta.region_code", 1), ("published_at", -1)],
         "name": "regionCode_published_desc",
         "partial": {"source_meta.region_code": {"$exists": True}}},

        {"keys": [("source_meta.query_seed", 1), ("published_at", -1)],
         "name": "querySeed_published_desc",
         "partial": {"source_meta.query_seed": {"$exists": True, "$type": "string"}}},

        {"keys": [("source_meta.duration_bucket", 1), ("published_at", -1)],
         "name": "durationBucket_published_desc",
         "partial": {"source_meta.duration_bucket": {"$exists": True, "$type": "string"}}},

        # Growth analytics (coverage & growth phases)
        {"keys": [("growth_phase", 1), ("coverage_score", -1), ("last_snapshot_ts", -1)],
         "name": "growthPhase_coverage_lastSnap_desc"},

        # Wildcard indexes for flexible queries in dashboards / ML exploration
        # (MongoDB 4.2+). These are scoped to horizons & snapshot_features only.
        {"keys": [("horizons.$**", 1)],
         "name": "wild_horizons"},

        {"keys": [("snapshot_features.$**", 1)],
         "name": "wild_snapshotFeatures"},
    ],

    # === CHANNELS (unchanged, but kept for completeness) ===
    "channels": [
        {"keys": [("handle", 1)],
         "name": "handle_uniq",
         "unique": True,
         "partial": {"handle": {"$exists": True, "$type": "string"}}},

        {"keys": [("last_updated", -1)],
         "name": "lastUpdated_desc"},
    ],
}


# ------------- Helpers -------------
def _index_signature(ixdoc) -> Tuple[Tuple[str, int], ...]:
    """Return a tuple that uniquely identifies an index by its key ordering."""
    return tuple(ixdoc["key"].items())


def _existing_indexes(coll):
    return list(coll.list_indexes())


def create_or_verify_collection_indexes(db, coll_name, specs, show_only=False):
    coll = db[coll_name]
    existing = _existing_indexes(coll)

    # map theo key và theo name để tránh đụng nhau
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

        # 1) Nếu đã có 1 index với cùng key → bỏ qua (kệ nó tên gì)
        if key_tuple in existing_by_keys:
            ix = existing_by_keys[key_tuple]
            logging.info(
                f"   ⏭️  Skipped existing index with same keys: {keys} "
                f"(existing name={ix.get('name')})"
            )
            skipped += 1
            continue

        # 2) Nếu đã có index trùng name nhưng keys khác → cảnh báo & bỏ qua
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
            logging.info(
                f"   👀 Would create index: {keys}"
                + (f" [name={name}]" if name else "")
                + (" [unique]" if unique else "")
                + (f" [partial={partial}]" if partial else "")
            )
            continue

        opts = {"background": True}
        if name:
            opts["name"] = name
        if unique:
            opts["unique"] = True
        if partial:
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
            # code 85: IndexOptionsConflict (index với cùng key/name đã tồn tại khác options)
            if getattr(e, "code", None) == 85:
                logging.warning(
                    f"   ⚠️ Index conflict for {name or keys}: {e}. Skipping."
                )
                skipped += 1
            else:
                raise

    return created, skipped


def drop_unused_indexes(db, coll_name, keep_specs):
    coll = db[coll_name]
    existing = _existing_indexes(coll)
    keep_sig = {tuple(s["keys"]) for s in keep_specs}

    for ix in existing:
        if ix["name"] == "_id_":
            continue
        if _index_signature(ix) not in keep_sig:
            coll.drop_index(ix["name"])
            logging.info(f"   🗑️  Dropped old index: {ix['name']}")


def drop_all_indexes(db, coll_name, show_only=False):
    """Drop all indexes on a collection except the default _id_ index."""
    coll = db[coll_name]
    existing = _existing_indexes(coll)

    logging.info(f"\n🧨 Rebuilding indexes for collection: {coll_name}")
    for ix in existing:
        if ix["name"] == "_id_":
            continue
        if show_only:
            logging.info(
                f"   👀 Would drop index: {ix['name']} ({list(ix['key'].items())})"
            )
        else:
            coll.drop_index(ix["name"])
            logging.info(f"   🗑️  Dropped index: {ix['name']}")


# ------------- Main -------------


def main():
    args = parse_args()
    client, db = get_db(args.mongo_uri, args.db)

    if args.rebuild and args.drop_old:
        logging.warning(
            "⚠️ Both --rebuild and --drop-old were specified. "
            "--rebuild takes precedence for dropping indexes."
        )

    # collections to process
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
            logging.warning(f"⚠️  Unknown collection '{coll_name}' (skipped).")
            continue

        specs = INDEX_MAP[coll_name]

        # Nếu --rebuild: drop toàn bộ index trước rồi tạo lại
        if args.rebuild:
            drop_all_indexes(db, coll_name, show_only=args.show_only)

        # Tạo / verify index theo INDEX_MAP
        created, skipped = create_or_verify_collection_indexes(
            db, coll_name, specs, show_only=args.show_only
        )
        total_created += created
        total_skipped += skipped

        # Nếu không rebuild mà có --drop-old → drop những index không nằm trong INDEX_MAP
        if args.drop_old and not args.rebuild and not args.show_only:
            drop_unused_indexes(db, coll_name, specs)

    logging.info("\n✅ Index maintenance complete.")
    logging.info(f"   Total created: {total_created}")
    logging.info(f"   Total skipped: {total_skipped}\n")


if __name__ == "__main__":
    main()
