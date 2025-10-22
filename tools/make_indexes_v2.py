#!/usr/bin/env python3
"""
make_indexes_v2.py — Smart MongoDB Index Manager
------------------------------------------------
Purpose:
    This script manages all MongoDB indexes across multiple collections.
    It creates missing indexes, skips existing ones, and can optionally
    remove outdated indexes that are no longer part of the standard map.

Main Features:
    ✅ Create or verify indexes for multiple collections (videos, channels, processed)
    ✅ Skip existing indexes automatically
    ✅ Optional cleanup of outdated indexes (--drop-old)
    ✅ Preview-only mode (--show-only)
    ✅ Safe to run multiple times (idempotent)
    ✅ Logs actions to both console and file

Example Usage:
---------------
# Create all default indexes
python make_indexes_v2.py

# Show what would be created, without applying changes
python make_indexes_v2.py --show-only

# Remove old indexes not listed in INDEX_MAP
python make_indexes_v2.py --drop-old

# Apply only to specific collections
python make_indexes_v2.py --collections videos,channels
"""

import os
import argparse
import logging
from pymongo import MongoClient

# ----------------------------------------------------
# 1. Logging configuration
# ----------------------------------------------------
logging.basicConfig(
    filename="index_maintenance.log",
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger("").addHandler(console)

# ----------------------------------------------------
# 2. MongoDB setup
# ----------------------------------------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")
client = MongoClient(MONGO_URI)
db = client.get_database()

# ----------------------------------------------------
# 3. Index definitions
# ----------------------------------------------------
INDEX_MAP = {
    "videos": [
        # For tracking scheduler (fast lookup by status and next poll)
        [("tracking.status", 1), ("tracking.next_poll_after", 1)],
        # For sorting by published date (most recent first)
        [("snippet.publishedAt", -1)],
        # For filtering by region or geo source
        [("source.regionCode", 1)],
        # For channel-level grouping
        [("snippet.channelId", 1)],
        # For quick video duration filtering
        [("snippet.lengthBucket", 1)],
        # For category analytics
        [("snippet.categoryId", 1)],
    ],
    "channels": [
        # Default primary key
        [("_id", 1)],
        # Channel handle lookup
        [("handle", 1)],
        # For detecting stale channels
        [("last_checked_at", -1)],
    ],
    "processed": [
        # Fast lookup by video_id
        [("video_id", 1)],
        # Query by processing status
        [("status", 1)],
        # Sort or filter by snapshot time
        [("snapshot_time", -1)],
    ],
}

# ----------------------------------------------------
# 4. Helper functions
# ----------------------------------------------------
def get_existing_index_keys(coll):
    """Return a set of key tuples representing existing indexes."""
    return {tuple(ix["key"].items()) for ix in coll.list_indexes()}


def create_or_verify_indexes(collection_name, indexes, show_only=False):
    """
    Create any missing indexes for a collection.
    Skip those that already exist.
    """
    coll = db[collection_name]
    existing = get_existing_index_keys(coll)

    logging.info(f"\n📂 Collection: {collection_name}")
    created = 0
    skipped = 0

    for fields in indexes:
        key_tuple = tuple(fields)
        if key_tuple in existing:
            logging.info(f"   ⏭️  Skipped existing index: {fields}")
            skipped += 1
        else:
            if show_only:
                logging.info(f"   👀 Would create index: {fields}")
            else:
                coll.create_index(fields, background=True)
                logging.info(f"   ✅ Created index: {fields}")
                created += 1

    return created, skipped


def drop_unused_indexes(collection_name, keep_list):
    """
    Remove indexes that are not in the official INDEX_MAP.
    Keeps the default _id_ index automatically.
    """
    coll = db[collection_name]
    existing = list(coll.list_indexes())
    keep_set = {tuple(k) for sublist in keep_list for k in sublist}

    for ix in existing:
        if ix["name"] == "_id_":
            continue
        if tuple(ix["key"].items()) not in keep_set:
            coll.drop_index(ix["name"])
            logging.info(f"   🗑️  Dropped old index: {ix['name']}")

# ----------------------------------------------------
# 5. Main CLI logic
# ----------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Smart MongoDB index manager.")
    parser.add_argument("--show-only", action="store_true", help="Show what would be done, but make no changes.")
    parser.add_argument("--drop-old", action="store_true", help="Drop indexes not in the official INDEX_MAP.")
    parser.add_argument("--collections", type=str, default="all", help="Comma-separated list of collections (default: all).")
    args = parser.parse_args()

    # Determine which collections to process
    collections_to_process = (
        list(INDEX_MAP.keys())
        if args.collections.lower() == "all"
        else [c.strip() for c in args.collections.split(",")]
    )

    total_created = 0
    total_skipped = 0

    logging.info("🚀 Starting MongoDB index maintenance...\n")

    for coll_name in collections_to_process:
        if coll_name not in INDEX_MAP:
            logging.warning(f"⚠️  Unknown collection '{coll_name}' (skipped).")
            continue

        indexes = INDEX_MAP[coll_name]
        created, skipped = create_or_verify_indexes(coll_name, indexes, args.show_only)
        total_created += created
        total_skipped += skipped

        if args.drop_old and not args.show_only:
            drop_unused_indexes(coll_name, indexes)

    logging.info("\n✅ Index maintenance complete.")
    logging.info(f"   Total created: {total_created}")
    logging.info(f"   Total skipped: {total_skipped}\n")


if __name__ == "__main__":
    main()
