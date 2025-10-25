#!/usr/bin/env python3
"""
make_indexes_v3.py — Smart MongoDB Index Manager (v3)
-----------------------------------------------------
- Creates/verifies compound & partial indexes tailored for YT-AUTOSCANNER.
- Idempotent, preview mode, optional cleanup of legacy indexes.

Usage:
  python make_indexes_v3.py                   # create/verify all
  python make_indexes_v3.py --show-only       # dry run
  python make_indexes_v3.py --drop-old        # drop indexes not in INDEX_MAP
  python make_indexes_v3.py --collections videos,processed
"""

import os
import argparse
import logging
from pymongo import MongoClient

# ---------------- Logging ----------------
logging.basicConfig(
    filename="index_maintenance.log",
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger("").addHandler(console)

# ---------------- Mongo ----------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")
client = MongoClient(MONGO_URI)
db = client.get_database()

# ---------------- Index Map ----------------
# Each index spec:
# {"keys": [("field", 1 or -1), ...], "name": "optional", "unique": bool, "partial": dict}
INDEX_MAP = {
    "videos": [
        # Queue for tracker (full)
        {"keys": [("tracking.status", 1), ("tracking.next_poll_after", 1)],
         "name": "trackStatus_nextPoll"},

        # Queue for tracker (active only) — smaller, faster scans
        {"keys": [("tracking.status", 1), ("tracking.next_poll_after", 1)],
         "name": "trackStatus_nextPoll_activeOnly",
         "partial": {"tracking.status": {"$in": ["queued", "tracking", "retry"]}}},

        # Latest videos per channel
        {"keys": [("snippet.channelId", 1), ("snippet.publishedAt", -1)],
         "name": "channelId_publishedAt_desc"},

        # Region + time (discover/report)
        {"keys": [("source.regionCode", 1), ("snippet.publishedAt", -1)],
         "name": "region_publishedAt_desc"},

        # Category + length + time (analytics)
        {"keys": [("snippet.categoryId", 1),
                  ("snippet.lengthBucket", 1),
                  ("snippet.publishedAt", -1)],
         "name": "category_lengthBucket_publishedAt_desc"},

        # Plain time sort (keep only if you still query by time alone)
        {"keys": [("snippet.publishedAt", -1)],
         "name": "publishedAt_desc"},
    ],

    "processed": [
        # video_id should be unique here (processed is 1-1 by video)
        {"keys": [("video_id", 1)], "name": "uniq_video_id", "unique": True},
        {"keys": [("status", 1), ("last_snapshot_ts", -1)],
         "name": "status_lastSnapshot_desc"},
        {"keys": [("last_snapshot_ts", -1)],
         "name": "lastSnapshot_desc"},
    ],

    "channels": [
        # _id is channel_id in your schema; keep unique by default.
        # Lookups by handle (if present)
        {"keys": [("handle", 1)], "name": "handle_uniq", "unique": True,
         "partial": {"handle": {"$exists": True, "$type": "string"}}},
        {"keys": [("last_updated", -1)], "name": "lastUpdated_desc"},
    ],
}

# ------------- Helpers -------------
def _index_signature(ixdoc):
    """Return a tuple that uniquely identifies an index by its key ordering."""
    return tuple(ixdoc["key"].items())

def _spec_signature(spec):
    return tuple(spec["keys"])

def _existing_indexes(coll):
    return list(coll.list_indexes())

def create_or_verify_collection_indexes(coll_name, specs, show_only=False):
    coll = db[coll_name]
    existing = _existing_indexes(coll)
    existing_sigs = {_index_signature(ix) for ix in existing}

    logging.info(f"\n📂 Collection: {coll_name}")
    created = skipped = 0

    for spec in specs:
        keys = spec["keys"]
        name = spec.get("name")
        unique = spec.get("unique", False)
        partial = spec.get("partial")
        key_tuple = tuple(keys)

        if key_tuple in existing_sigs:
            # Already exists with these keys (ignore name/options differences)
            logging.info(f"   ⏭️  Skipped existing index: {keys}")
            skipped += 1
            continue

        if show_only:
            logging.info(f"   👀 Would create index: {keys}"
                         + (f" [name={name}]" if name else "")
                         + (" [unique]" if unique else "")
                         + (f" [partial={partial}]" if partial else ""))
            continue

        opts = {"background": True}
        if name:
            opts["name"] = name
        if unique:
            opts["unique"] = True
        if partial:
            opts["partialFilterExpression"] = partial

        coll.create_index(keys, **opts)
        logging.info(f"   ✅ Created index: {keys}"
                     + (f" [name={name}]" if name else "")
                     + (" [unique]" if unique else "")
                     + (f" [partial]" if partial else ""))
        created += 1

    return created, skipped

def drop_unused_indexes(coll_name, keep_specs):
    coll = db[coll_name]
    existing = _existing_indexes(coll)
    keep_sig = {tuple(s["keys"]) for s in keep_specs}

    for ix in existing:
        if ix["name"] == "_id_":
            continue
        if _index_signature(ix) not in keep_sig:
            coll.drop_index(ix["name"])
            logging.info(f"   🗑️  Dropped old index: {ix['name']}")

# ------------- CLI -------------
def main():
    parser = argparse.ArgumentParser(description="Smart MongoDB index manager (v3).")
    parser.add_argument("--show-only", action="store_true",
                        help="Show what would be done, but make no changes.")
    parser.add_argument("--drop-old", action="store_true",
                        help="Drop indexes not in the official INDEX_MAP.")
    parser.add_argument("--collections", type=str, default="all",
                        help="Comma-separated list of collections (default: all).")
    args = parser.parse_args()

    collections = (list(INDEX_MAP.keys())
                   if args.collections.lower() == "all"
                   else [c.strip() for c in args.collections.split(",")])

    total_created = total_skipped = 0
    logging.info("🚀 Starting MongoDB index maintenance...\n")

    for coll_name in collections:
        if coll_name not in INDEX_MAP:
            logging.warning(f"⚠️  Unknown collection '{coll_name}' (skipped).")
            continue

        specs = INDEX_MAP[coll_name]
        created, skipped = create_or_verify_collection_indexes(
            coll_name, specs, show_only=args.show_only
        )
        total_created += created
        total_skipped += skipped

        if args.drop_old and not args.show_only:
            drop_unused_indexes(coll_name, specs)

    logging.info("\n✅ Index maintenance complete.")
    logging.info(f"   Total created: {total_created}")
    logging.info(f"   Total skipped: {total_skipped}\n")

if __name__ == "__main__":
    main()