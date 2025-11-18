#!/usr/bin/env python3
"""
worker/compute_dashboard_kpis.py (v3)

Background worker that computes dashboard KPIs from `videos`
and stores them into `dashboard_kpis` (materialized KPI snapshots).

- Aggregates global metrics (total videos, channels, tracking status).
- Counts low-quality related stop reasons and ML flags (3h + 6h).
- Keeps only the latest 100 KPI snapshots.
- Updates `worker_runs` as a lightweight heartbeat.
"""

from __future__ import annotations

import sys
import logging
from datetime import datetime, timezone
from typing import Dict, Any

from pymongo.collection import Collection

from config.env import load_env          # ensure .env is loaded once
from config.db import get_db             # central DB helper


# =========================
# Logging setup
# =========================

logger = logging.getLogger("compute_dashboard_kpis")
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter(
    "[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
)
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


# =========================
# KPI aggregation pipeline
# =========================

def build_kpi_pipeline() -> list:
    """
    Build the Mongo aggregation pipeline to compute global KPIs
    from the `videos` collection.

    Output fields:
      - total_videos
      - total_channels
      - tracking_active
      - completed_total
      - stopped_total
      - completed_age24
      - completed_removed
      - stopped_low_quality
      - low_quality_flagged (3h OR 6h ML flags)
    """
    return [
        {
            "$group": {
                "_id": None,
                "total_videos": {"$sum": 1},

                # Unique channels
                "total_channels_set": {"$addToSet": "$snippet.channelId"},

                # Status counters
                "tracking_active": {
                    "$sum": {"$cond": [{"$eq": ["$tracking.status", "tracking"]}, 1, 0]}
                },
                "completed_total": {
                    "$sum": {"$cond": [{"$eq": ["$tracking.status", "complete"]}, 1, 0]}
                },
                "stopped_total": {
                    "$sum": {"$cond": [{"$eq": ["$tracking.status", "stopped"]}, 1, 0]}
                },

                # Complete due to age >= 24h
                "completed_age24": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$eq": ["$tracking.status", "complete"]},
                                    {"$eq": ["$tracking.stop_reason", "age>=24h"]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # Completed due to removal / unavailable / deleted / not_found
                "completed_removed": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$eq": ["$tracking.status", "complete"]},
                                    {
                                        "$in": [
                                            "$tracking.stop_reason",
                                            ["removed", "unavailable", "deleted", "not_found"],
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # Stopped due to any low_quality-related stop_reason
                "stopped_low_quality": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$eq": ["$tracking.status", "stopped"]},
                                    {
                                        "$gt": [
                                            {
                                                "$indexOfBytes": [
                                                    "$tracking.stop_reason",
                                                    "low_quality",
                                                ]
                                            },
                                            -1,
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # ML flags: any video where 3h OR 6h model flagged is_low
                "low_quality_flagged": {
                    "$sum": {
                        "$cond": [
                            {
                                "$or": [
                                    {"$eq": ["$ml_flags.low_quality_v1_3h.is_low", True]},
                                    {"$eq": ["$ml_flags.low_quality_v1_3h.is_low", 1]},
                                    {"$eq": ["$ml_flags.low_quality_v3_6h.is_low", True]},
                                    {"$eq": ["$ml_flags.low_quality_v3_6h.is_low", 1]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "total_videos": 1,
                "total_channels": {"$size": "$total_channels_set"},
                "tracking_active": 1,
                "completed_total": 1,
                "stopped_total": 1,
                "completed_age24": 1,
                "completed_removed": 1,
                "stopped_low_quality": 1,
                "low_quality_flagged": 1,
            }
        },
    ]


def compute_kpis(videos_col: Collection) -> Dict[str, Any]:
    """Run the KPI aggregation pipeline on the `videos` collection."""
    logger.info("Running KPI aggregation…")
    pipeline = build_kpi_pipeline()

    result = list(videos_col.aggregate(pipeline, allowDiskUse=True))
    if not result:
        logger.warning("Aggregation returned no results. Using zeros.")
        return {
            "total_videos": 0,
            "total_channels": 0,
            "tracking_active": 0,
            "completed_total": 0,
            "stopped_total": 0,
            "completed_age24": 0,
            "completed_removed": 0,
            "stopped_low_quality": 0,
            "low_quality_flagged": 0,
        }

    return result[0]


def save_snapshot(kpis_col: Collection, kpi_doc: Dict[str, Any]) -> None:
    """
    Insert a KPI snapshot into `dashboard_kpis` and keep only
    the latest 100 snapshots (by _id).
    """
    kpi = dict(kpi_doc)
    kpi["ts"] = datetime.now(timezone.utc)

    res = kpis_col.insert_one(kpi)
    logger.info("Saved KPI snapshot: %s at %s", res.inserted_id, kpi["ts"].isoformat())

    # Cleanup: keep only latest 100 snapshots
    try:
        latest_ids = [
            d["_id"]
            for d in kpis_col.find({}, {"_id": 1})
                            .sort("_id", -1)
                            .limit(100)
        ]

        delete_result = kpis_col.delete_many({"_id": {"$nin": latest_ids}})

        logger.info(
            "Cleanup: removed %s old KPI snapshots",
            delete_result.deleted_count,
        )

    except Exception as cleanup_err:
        logger.warning("KPI cleanup failed: %s", cleanup_err)


def main() -> int:
    logger.info("Starting compute_dashboard_kpis worker…")

    # Ensure .env is loaded before DB access
    load_env()

    db = get_db()
    videos = db.videos
    kpis_col = db.dashboard_kpis

    kpis = compute_kpis(videos)

    logger.info(
        "KPIs: videos=%s | channels=%s | tracking=%s | complete=%s | stopped=%s",
        kpis["total_videos"],
        kpis["total_channels"],
        kpis["tracking_active"],
        kpis["completed_total"],
        kpis["stopped_total"],
    )

    save_snapshot(kpis_col, kpis)

    # Heartbeat for Overview: update worker_runs
    try:
        db.worker_runs.update_one(
            {"name": "compute_dashboard_kpis"},
            {"$set": {"last_run": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception as e:
        logger.warning("Failed to update worker_runs: %s", e)

    logger.info("Worker completed OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
