#!/usr/bin/env python3
"""
make_indexes.py (clean version: no process_video indexes)

This script creates all required MongoDB indexes for the full YT AutoScanner
pipeline: discover → track → low_quality → viral_prediction → finalize → dashboard.

All "process_video" indexes have been removed as requested.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Dict, Tuple

from pymongo import MongoClient
from pymongo.errors import OperationFailure

from config.env import load_env, get_env


# ============================================================
# Define indexes for each collection
# ============================================================

def indexes_videos() -> List[Dict[str, any]]:
    """
    Index set for the `videos` collection.
    Clean, updated schema (no process_video indexes).
    """
    return [

        # -------------------------------------------------
        # Discover phase: avoid inserting duplicates
        # -------------------------------------------------
        {
            "keys": [("video_id", 1)],
            "name": "videoId_unique",
            "unique": True,
        },

        # -------------------------------------------------
        # Track phase: quickly pull videos still being tracked
        # -------------------------------------------------
        {
            "keys": [
                ("tracking.status", 1),
                ("tracking.next_poll_ts", 1),
            ],
            "name": "track_status_nextPoll",
        },

        # Helping finalize / age filters
        {
            "keys": [("latest_stats_ts", 1)],
            "name": "latestStatsTs",
        },

        # -------------------------------------------------
        # Low-quality models (3h & 6h)
        # -------------------------------------------------
        # 3h stage: videos age >= 3h AND not scored yet
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.low_quality_v1_3h.score", 1),
            ],
            "name": "lowq_3h_snap0_score",
        },

        # 6h stage: videos age >= 6h AND not scored yet
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.low_quality_v3_6h.score", 1),
            ],
            "name": "lowq_6h_snap0_score",
        },

        # -------------------------------------------------
        # Viral prediction 6h / 12h / 24h
        # -------------------------------------------------

        # 6h
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.viral_v2.h6.score_proba", 1),
            ],
            "name": "viral_h6_snap0_score",
        },

        # 12h
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.viral_v2.h12.score_proba", 1),
            ],
            "name": "viral_h12_snap0_score",
        },

        # 24h — cleaned: now using h24 not h24_validation
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.viral_v2.h24.score_proba", 1),
            ],
            "name": "viral_h24_snap0_score",
        },

        # -------------------------------------------------
        # Finalize-phase support
        # -------------------------------------------------

        # Quickly query by final.status + sort by newest
        {
            "keys": [
                ("ml_flags.viral_v2.final.status", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "viral_finalStatus_publishedAt_desc",
        },

        # For sorting by newest activity
        {
            "keys": [
                ("latest_stats_ts", -1),
            ],
            "name": "latestStatsTs_desc",
        },

        # Optional: sort by publishedAt for browsing / filter UI
        {
            "keys": [
                ("snippet.publishedAt", -1),
            ],
            "name": "publishedAt_desc",
        },
    ]


# ============================================================
# Index creation
# ============================================================

def create_indexes(coll, indexes):
    """
    Create indexes for a MongoDB collection.
    """
    print(f"\n[Creating indexes on collection `{coll.name}`]")

    for idx in indexes:
        keys = idx["keys"]
        name = idx.get("name")
        unique = idx.get("unique", False)

        try:
            coll.create_index(keys, name=name, unique=unique, background=True)
            print(f"  ✔ Created index: {name}")
        except OperationFailure as e:
            print(f"  ✖ Failed to create {name}: {e}")


def drop_all_indexes(coll):
    """
    Drop ALL indexes except the default _id_.
    """
    print(f"\n[Dropping ALL indexes for `{coll.name}`]")

    for idx in coll.list_indexes():
        idx_name = idx["name"]
        if idx_name == "_id_":
            continue
        try:
            coll.drop_index(idx_name)
            print(f"  ✔ Dropped index: {idx_name}")
        except Exception as e:
            print(f"  ✖ Failed to drop {idx_name}: {e}")


# ============================================================
# Main
# ============================================================

def main():
    load_env()
    mongo_uri = get_env("MONGO_URI")
    db_name = get_env("DB_NAME")

    parser = argparse.ArgumentParser(description="Create MongoDB indexes for AutoScanner.")
    parser.add_argument(
        "--collections",
        nargs="+",
        default=["videos"],
        help="Which collections to update (default: videos)",
    )
    parser.add_argument(
        "--drop-old",
        action="store_true",
        help="Drop old indexes before creating new ones",
    )

    args = parser.parse_args()

    client = MongoClient(mongo_uri)
    db = client[db_name]

    for cname in args.collections:
        coll = db[cname]

        if args.drop_old:
            drop_all_indexes(coll)

        if cname == "videos":
            create_indexes(coll, indexes_videos())
        else:
            print(f"(No index rules defined for `{cname}` — skipped)")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
