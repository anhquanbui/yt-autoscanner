#!/usr/bin/env python3
"""
make_indexes.py — Smart MongoDB Index Manager (v7, optimized for the new pipeline)

Based on your original v6 script, with the following updates:
    • Uses h24 (ml_flags.viral_v2.h24.*) instead of h24_validation.
    • Uses tracking.next_poll_ts (next_poll_after has been removed).
    • All indexes for processed_videos / process_video have been removed by design.

Still retained:
    • config.env loader (load_env / get_env)
    • Smart index manager options:
        --show-only, --drop-old, --rebuild, --collections
    • Optimized indexes for:
        - Tracking queue (track_once)
        - Low-quality workers (3h & 6h)
        - Viral_v2 pipeline (6h / 12h / 24h + final)
        - Video discovery by region / query / channel
"""

from __future__ import annotations

import argparse
import logging
from typing import Dict, List, Tuple

from pymongo import MongoClient
from pymongo.errors import OperationFailure

from config.env import load_env, get_env


# ---------------- Logging (console only) ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)


# ---------------- CLI ----------------
def parse_args() -> argparse.Namespace:
    """
    Parse CLI arguments and resolve defaults from environment variables.
    """
    load_env()

    p = argparse.ArgumentParser(
        description="Smart MongoDB index manager (v7)."
    )

    p.add_argument(
        "--mongo-uri",
        default=get_env("MONGO_URI", "mongodb://127.0.0.1:27017/ytscan"),
        help=(
            "MongoDB connection URI "
            "(default: from MONGO_URI or "
            "mongodb://127.0.0.1:27017/ytscan)."
        ),
    )
    p.add_argument(
        "--db",
        default=get_env("MONGO_DB"),
        help=(
            "Database name. "
            "Default: from MONGO_DB or inferred from URI "
            "(e.g. mongodb://host:27017/ytscan → ytscan)."
        ),
    )
    p.add_argument(
        "--show-only",
        action="store_true",
        help="Show planned actions without applying any changes.",
    )
    p.add_argument(
        "--drop-old",
        action="store_true",
        help="Drop existing indexes not defined in INDEX_MAP.",
    )
    p.add_argument(
        "--rebuild",
        action="store_true",
        help=(
            "Drop all non-_id_ indexes on selected collections "
            "and recreate indexes defined in INDEX_MAP."
        ),
    )
    p.add_argument(
        "--collections",
        type=str,
        default="all",
        help="Comma-separated list of collections (default: all in INDEX_MAP).",
    )

    return p.parse_args()


# ---------------- Mongo helpers ----------------
def _infer_db_name_from_uri(uri: str, fallback: str = "ytscan") -> str:
    """
    Infer database name from the MongoDB URI.
    """
    tail = uri.rsplit("/", 1)[-1]
    db_part = tail.split("?", 1)[0]
    return db_part or fallback


def connect_db(mongo_uri: str, explicit_db: str | None):
    """
    Create MongoDB client and resolve target database.
    """
    client = MongoClient(mongo_uri)
    if explicit_db:
        db = client[explicit_db]
    else:
        db_name = _infer_db_name_from_uri(mongo_uri, fallback="ytscan")
        db = client[db_name]
    return client, db


# ---------------- Index Map ----------------
# Structure:
#   INDEX_MAP: Dict[str, List[dict]]
#   Each index spec:
#     {
#         "keys":   [("field", 1 or -1), ...],
#         "name":   "optional_index_name",
#         "unique": bool,
#         "partial": dict (partialFilterExpression),
#     }
INDEX_MAP: Dict[str, List[dict]] = {
    # === SOURCE COLLECTIONS ===
    "videos": [
        # --------------------------------------------------
        # Tracking queue (track_once)
        # --------------------------------------------------
        # Queue index:
        #   (tracking.status, tracking.next_poll_ts)
        #   Partial index for active tracking states.
        {
            "keys": [
                ("tracking.status", 1),
                ("tracking.next_poll_ts", 1),
            ],
            "name": "trackStatus_nextPoll_active",
            "partial": {
                "tracking.status": {"$in": ["queued", "tracking", "retry"]},
            },
        },
        # Filter by tracking status and sort by publish date.
        {
            "keys": [
                ("tracking.status", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "trackStatus_publishedAt_desc",
        },
        # Channel-level queries ordered by publish time.
        {
            "keys": [
                ("snippet.channelId", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "channelId_publishedAt_desc",
        },
        # Region-based discovery and analytics.
        {
            "keys": [
                ("source.regionCode", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "region_publishedAt_desc",
        },
        # Query-seed discovery index.
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
        # Category and length-bucket filtering.
        {
            "keys": [
                ("snippet.categoryId", 1),
                ("snippet.lengthBucket", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "category_lengthBucket_publishedAt_desc",
        },
        # Global recency index.
        {
            "keys": [
                ("snippet.publishedAt", -1),
            ],
            "name": "publishedAt_desc",
        },

        # --------------------------------------------------
        # Shared base for low-quality workers (3h & 6h)
        # --------------------------------------------------
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

        # --------------------------------------------------
        # low_quality_v3_6h ML pipeline
        # --------------------------------------------------
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

        # --------------------------------------------------
        # low_quality_v1_3h ML pipeline
        # --------------------------------------------------
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

        # ==================================================
        # Viral_v2 ML pipeline (6h / 12h / 24h + final)
        # ==================================================

        # 6h stage
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.viral_v2.h6.score_proba", 1),
            ],
            "name": "viral_h6_snap0_score",
        },

        # 12h stage
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.viral_v2.h12.score_proba", 1),
            ],
            "name": "viral_h12_snap0_score",
        },

        # 24h stage (new schema)
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.viral_v2.h24.score_proba", 1),
            ],
            "name": "viral_h24_snap0_score",
        },

        # Dashboard / analytics: final status + publish time.
        {
            "keys": [
                ("ml_flags.viral_v2.final.status", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "viral_finalStatus_publishedAt_desc",
        },

        # NEW: Final status + behavior + publish time
        # Used by Viral Filter page.
        {
            "keys": [
                ("ml_flags.viral_v2.final.status", 1),
                ("ml_flags.viral_v2.final.behavior", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "viral_finalStatus_behavior_publishedAt_desc",
        },

        # NEW: Final decided stage + publish time
        # Supports analysis of which stage made the decision.
        {
            "keys": [
                ("ml_flags.viral_v2.final.decided_stage", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "viral_finalDecidedStage_publishedAt_desc",
        },

        # Sort by latest stats timestamp (finalize / age filters).
        {
            "keys": [
                ("latest_stats_ts", -1),
            ],
            "name": "latestStatsTs_desc",
        },
    ],

    # === CHANNELS COLLECTION ===
    "channels": [
        {
            "keys": [("handle", 1)],
            "name": "handle_uniq",
            "unique": True,
            "partial": {
                "handle": {"$exists": True, "$type": "string"}
            },
        },
        {
            "keys": [("last_updated", -1)],
            "name": "lastUpdated_desc",
        },
    ],
}
