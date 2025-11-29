#!/usr/bin/env python3
"""
worker/compute_dashboard_kpis.py (v7)

Background worker that computes dashboard KPIs from `videos`
and stores them into `dashboard_kpis` (materialized KPI snapshots).

High-level responsibilities:
- Aggregate global metrics (total videos, channels, tracking status).
- Count low-quality related stop reasons.
- Compute ML coverage + flags for low_quality 3h / 6h.
- Compute Viral v1 KPIs (likely / confirmed).
- Compute Viral v2 KPIs:
    + Stage coverage per furthest stage (6h / 12h / 24h / Final, non-overlapping).
    + Extra details (candidates, 12h viral, etc).
- Keep only the latest 100 KPI snapshots so the collection does not grow forever.
- Update `worker_runs` as a lightweight heartbeat for the dashboard.
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

# Create a logger dedicated to this worker so logs are easy to filter
logger = logging.getLogger("compute_dashboard_kpis")

# Log to stdout (systemd will pick this up)
handler = logging.StreamHandler(sys.stdout)

# Consistent log format with timestamp + level
formatter = logging.Formatter(
    "[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
)
handler.setFormatter(formatter)

# Avoid attaching multiple handlers if the module is imported multiple times
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

    This is the *single source of truth* for all dashboard counters.
    Any change here should be mirrored on the dashboard pages.

    Output fields:
      - total_videos
      - total_channels

      - tracking_active
      - completed_total
      - stopped_total

      - completed_age24
      - completed_removed
      - stopped_low_quality

      - ml_3h_scored            (videos having a 3h low-quality score)
      - ml_6h_scored            (videos having a 6h low-quality score)
      - low_quality_flagged_3h  (is_low at 3h)
      - low_quality_flagged_6h  (is_low at 6h)
      - low_quality_flagged_any (is_low at 3h OR 6h)

      - viral_likely            (viral_v1.likely == True / 1)
      - viral_confirmed         (viral_v1.confirmed == True / 1)

      - viral2_h6_scored
      - viral2_h6_candidates
      - viral2_h12_scored
      - viral2_12h_viral
      - viral2_h24_scored

      - viral2_stage_6h_only    (furthest stage = 6h, non-overlap)
      - viral2_stage_12h_only   (furthest stage = 12h, non-overlap)
      - viral2_stage_24h_only   (furthest stage = 24h, non-overlap)

      - viral2_final_viral
      - viral2_final_nonviral
      - viral2_final_nonviral_lowq
      - viral2_final_unknown
      - viral2_final_decided
    """
    return [
        {
            # Massive $group that aggregates everything in one pass.
            "$group": {
                "_id": None,
                "total_videos": {"$sum": 1},

                # Unique channels (we compute count later using $size)
                "total_channels_set": {"$addToSet": "$snippet.channelId"},

                # ===== Tracking status counters =====
                "tracking_active": {
                    "$sum": {"$cond": [{"$eq": ["$tracking.status", "tracking"]}, 1, 0]}
                },
                "completed_total": {
                    "$sum": {"$cond": [{"$eq": ["$tracking.status", "complete"]}, 1, 0]}
                },
                "stopped_total": {
                    "$sum": {"$cond": [{"$eq": ["$tracking.status", "stopped"]}, 1, 0]}
                },

                # Completed because age >= 24h (normal finish)
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

                # Completed because the video is gone: removed / unavailable / deleted / not_found
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
                # We check if "low_quality" appears inside stop_reason string.
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

                # ---------- ML coverage & flags (low_quality) ----------

                # Any numeric score at 3h
                "ml_3h_scored": {
                    "$sum": {
                        "$cond": [
                            {
                                "$ne": [
                                    "$ml_flags.low_quality_v1_3h.score",
                                    None,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # Any numeric score at 6h
                "ml_6h_scored": {
                    "$sum": {
                        "$cond": [
                            {
                                "$ne": [
                                    "$ml_flags.low_quality_v3_6h.score",
                                    None,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # Videos flagged low-quality at 3h
                "low_quality_flagged_3h": {
                    "$sum": {
                        "$cond": [
                            {
                                "$or": [
                                    {"$eq": ["$ml_flags.low_quality_v1_3h.is_low", True]},
                                    {"$eq": ["$ml_flags.low_quality_v1_3h.is_low", 1]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # Videos flagged low-quality at 6h
                "low_quality_flagged_6h": {
                    "$sum": {
                        "$cond": [
                            {
                                "$or": [
                                    {"$eq": ["$ml_flags.low_quality_v3_6h.is_low", True]},
                                    {"$eq": ["$ml_flags.low_quality_v3_6h.is_low", 1]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # Any low-quality flag at 3h OR 6h
                "low_quality_flagged_any": {
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

                # ---------- Viral v1 (legacy) ----------

                "viral_likely": {
                    "$sum": {
                        "$cond": [
                            {
                                "$or": [
                                    {"$eq": ["$ml_flags.viral_v1.likely", True]},
                                    {"$eq": ["$ml_flags.viral_v1.likely", 1]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                "viral_confirmed": {
                    "$sum": {
                        "$cond": [
                            {
                                "$or": [
                                    {"$eq": ["$ml_flags.viral_v1.confirmed", True]},
                                    {"$eq": ["$ml_flags.viral_v1.confirmed", 1]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # ---------- Viral v2 (new model) ----------

                # Any score at 6h stage
                "viral2_h6_scored": {
                    "$sum": {
                        "$cond": [
                            {
                                "$ne": [
                                    "$ml_flags.viral_v2.h6.score_proba",
                                    None,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # 6h candidates, but excluding those already confirmed viral at 12h
                "viral2_h6_candidates": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$or": [
                                            {"$eq": ["$ml_flags.viral_v2.h6.is_candidate", True]},
                                            {"$eq": ["$ml_flags.viral_v2.h6.is_candidate", 1]},
                                        ]
                                    },
                                    {
                                        # NOT (is_viral_12h == True/1)
                                        "$not": [
                                            {
                                                "$or": [
                                                    {"$eq": ["$ml_flags.viral_v2.h12.is_viral_12h", True]},
                                                    {"$eq": ["$ml_flags.viral_v2.h12.is_viral_12h", 1]},
                                                ]
                                            }
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # Any score at 12h stage
                "viral2_h12_scored": {
                    "$sum": {
                        "$cond": [
                            {
                                "$ne": [
                                    "$ml_flags.viral_v2.h12.score_proba",
                                    None,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # Marked viral at 12h stage
                "viral2_12h_viral": {
                    "$sum": {
                        "$cond": [
                            {
                                "$or": [
                                    {"$eq": ["$ml_flags.viral_v2.h12.is_viral_12h", True]},
                                    {"$eq": ["$ml_flags.viral_v2.h12.is_viral_12h", 1]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # Any score at 24h validation stage
                "viral2_h24_scored": {
                    "$sum": {
                        "$cond": [
                            {
                                "$ne": [
                                    "$ml_flags.viral_v2.h24_validation.score_proba",
                                    None,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # ---------- Viral v2 non-overlapping stages ----------
                # We treat "furthest stage" as:
                #   - 6h only   (has 6h score, no 12h / 24h score, final unknown/None)
                #   - 12h only  (has 12h score, no 24h score, final unknown/None)
                #   - 24h only  (has 24h validation score, final unknown/None)

                "viral2_stage_6h_only": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$ne": [
                                            "$ml_flags.viral_v2.h6.score_proba",
                                            None,
                                        ]
                                    },
                                    {
                                        "$eq": [
                                            "$ml_flags.viral_v2.h12.score_proba",
                                            None,
                                        ]
                                    },
                                    {
                                        "$eq": [
                                            "$ml_flags.viral_v2.h24_validation.score_proba",
                                            None,
                                        ]
                                    },
                                    {
                                        "$or": [
                                            {"$eq": ["$ml_flags.viral_v2.final.status", None]},
                                            {"$eq": ["$ml_flags.viral_v2.final.status", "unknown"]},
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                "viral2_stage_12h_only": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$ne": [
                                            "$ml_flags.viral_v2.h12.score_proba",
                                            None,
                                        ]
                                    },
                                    {
                                        "$eq": [
                                            "$ml_flags.viral_v2.h24_validation.score_proba",
                                            None,
                                        ]
                                    },
                                    {
                                        "$or": [
                                            {"$eq": ["$ml_flags.viral_v2.final.status", None]},
                                            {"$eq": ["$ml_flags.viral_v2.final.status", "unknown"]},
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                "viral2_stage_24h_only": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$ne": [
                                            "$ml_flags.viral_v2.h24_validation.score_proba",
                                            None,
                                        ]
                                    },
                                    {
                                        "$or": [
                                            {"$eq": ["$ml_flags.viral_v2.final.status", None]},
                                            {"$eq": ["$ml_flags.viral_v2.final.status", "unknown"]},
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # ---------- Final decision breakdown ----------
                "viral2_final_viral": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$ml_flags.viral_v2.final.status", "viral"]},
                            1,
                            0,
                        ]
                    }
                },
                "viral2_final_nonviral": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$ml_flags.viral_v2.final.status", "non_viral"]},
                            1,
                            0,
                        ]
                    }
                },
                "viral2_final_nonviral_lowq": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$ml_flags.viral_v2.final.status", "non_viral_lowq"]},
                            1,
                            0,
                        ]
                    }
                },

                # Unknown = not finalized AND not already "removed/unavailable/deleted/not_found".
                "viral2_final_unknown": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$or": [
                                            {"$eq": ["$ml_flags.viral_v2.final.status", "unknown"]},
                                            {"$eq": ["$ml_flags.viral_v2.final.status", None]},
                                        ]
                                    },
                                    {
                                        "$not": [
                                            {
                                                "$in": [
                                                    "$tracking.stop_reason",
                                                    ["removed", "unavailable", "deleted", "not_found"],
                                                ]
                                            }
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # "Decided" = final.status is not None/unknown (any concrete label).
                "viral2_final_decided": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ne": ["$ml_flags.viral_v2.final.status", "unknown"]},
                                    {"$ne": ["$ml_flags.viral_v2.final.status", None]},
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
            # Final shape of the KPI document stored on dashboard_kpis
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

                "ml_3h_scored": 1,
                "ml_6h_scored": 1,
                "low_quality_flagged_3h": 1,
                "low_quality_flagged_6h": 1,
                "low_quality_flagged_any": 1,

                "viral_likely": 1,
                "viral_confirmed": 1,

                "viral2_h6_scored": 1,
                "viral2_h6_candidates": 1,
                "viral2_h12_scored": 1,
                "viral2_12h_viral": 1,
                "viral2_h24_scored": 1,

                "viral2_stage_6h_only": 1,
                "viral2_stage_12h_only": 1,
                "viral2_stage_24h_only": 1,

                "viral2_final_viral": 1,
                "viral2_final_nonviral": 1,
                "viral2_final_nonviral_lowq": 1,
                "viral2_final_unknown": 1,
                "viral2_final_decided": 1,
            }
        },
    ]


def compute_kpis(videos_col: Collection) -> Dict[str, Any]:
    """
    Run the KPI aggregation pipeline on the `videos` collection.

    Returns a flat dict with all KPI fields. If the aggregation returns
    no result (e.g., empty collection), we return a zero-filled struct
    so the dashboard code does not crash on missing keys.
    """
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
            "ml_3h_scored": 0,
            "ml_6h_scored": 0,
            "low_quality_flagged_3h": 0,
            "low_quality_flagged_6h": 0,
            "low_quality_flagged_any": 0,
            "viral_likely": 0,
            "viral_confirmed": 0,
            "viral2_h6_scored": 0,
            "viral2_h6_candidates": 0,
            "viral2_h12_scored": 0,
            "viral2_12h_viral": 0,
            "viral2_h24_scored": 0,
            "viral2_stage_6h_only": 0,
            "viral2_stage_12h_only": 0,
            "viral2_stage_24h_only": 0,
            "viral2_final_viral": 0,
            "viral2_final_nonviral": 0,
            "viral2_final_nonviral_lowq": 0,
            "viral2_final_unknown": 0,
            "viral2_final_decided": 0,
        }

    return result[0]


def save_snapshot(kpis_col: Collection, kpi_doc: Dict[str, Any]) -> None:
    """
    Insert a KPI snapshot into `dashboard_kpis` and keep only
    the latest 100 snapshots (by _id).

    This is a simple retention policy so the collection does not grow
    indefinitely while still allowing short-term historical views.
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
    """
    Entry point for the worker.

    Steps:
      1. Load env + connect to DB.
      2. Run KPI aggregation pipeline against `videos`.
      3. Save snapshot to `dashboard_kpis`.
      4. Update `worker_runs` heartbeat for the dashboard.
    """
    logger.info("Starting compute_dashboard_kpis worker…")

    # Ensure .env is loaded before DB access (URI, DB_NAME, etc.)
    load_env()

    db = get_db()
    videos = db.videos
    kpis_col = db.dashboard_kpis

    # Run aggregation
    kpis = compute_kpis(videos)

    # Log a compact summary for debugging / sanity checks
    logger.info(
        "KPIs: videos=%s | channels=%s | tracking=%s | complete=%s | stopped=%s | "
        "ml3h_scored=%s | ml6h_scored=%s | low3h=%s | low6=%s | "
        "viral_v1_likely=%s | viral_v1_confirmed=%s | "
        "viral2_stage_6h_only=%s | viral2_stage_12h_only=%s | viral2_stage_24h_only=%s | "
        "viral2_final_decided=%s",
        kpis["total_videos"],
        kpis["total_channels"],
        kpis["tracking_active"],
        kpis["completed_total"],
        kpis["stopped_total"],
        kpis["ml_3h_scored"],
        kpis["ml_6h_scored"],
        kpis["low_quality_flagged_3h"],
        kpis["low_quality_flagged_6h"],
        kpis["viral_likely"],
        kpis["viral_confirmed"],
        kpis["viral2_stage_6h_only"],
        kpis["viral2_stage_12h_only"],
        kpis["viral2_stage_24h_only"],
        kpis["viral2_final_decided"],
    )

    # Persist snapshot + enforce retention
    save_snapshot(kpis_col, kpis)

    # Heartbeat for Overview: update worker_runs so dashboard
    # can show "last run" and detect stale worker.
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
    # SystemExit + numeric code so cron/systemd can see success/failure.
    raise SystemExit(main())
