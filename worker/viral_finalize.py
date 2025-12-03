#!/usr/bin/env python3
"""
viral_finalize.py — finalize ml_flags.viral_v2.final.* based on h6/h12/h24 scores.

This worker:
- Reads ml_flags.viral_v2.h6 / h12 / h24 (fallback h24_validation for legacy docs).
- Applies thresholds + multiclass top_class to decide final.status.
- Handles low-quality blocked videos -> non_viral_lowq.
- Handles removed videos with dedicated statuses:
    * viral_after_removed  — video was viral (weak/viral/super) but later removed.
    * removed              — video removed without strong viral evidence.
- Attaches tracking metadata at finalize time:
    * tracking_status_at_final
    * tracking_stop_reason_at_final
    * is_removed_at_final
    * base_status          — original viral/non_viral label before "removed" wrapping.
"""

from __future__ import annotations

import os
import sys
import io
import argparse
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from pymongo import MongoClient

from config.env import load_env, get_env
from config.db import get_db as get_db_from_config


# Ensure UTF-8 console logging (helps with unicode titles / logs)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# =========================
# Helper functions
# =========================

def now_utc() -> datetime:
    """Return current time as timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def parse_iso8601(s: str | None) -> Optional[datetime]:
    """
    Parse an ISO-8601 datetime string (e.g. '2023-01-01T12:34:56Z')
    into a timezone-aware UTC datetime.

    Returns None if parsing fails.
    """
    if not s:
        return None
    try:
        # Replace trailing 'Z' with '+00:00' so datetime.fromisoformat can handle it.
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


# -------------------------
# DB resolve
# -------------------------

def resolve_db(cli_uri: str | None, cli_db: str | None):
    """
    Resolve Mongo client + DB with the following priority:

    1. If CLI passes --mongo-uri or --db → respect those values.
    2. Otherwise → use config.db.get_db(), which already respects env vars
       and any project-specific auth configuration.

    Returns:
        (client, db)
    """
    load_env()

    # Case 1: CLI override
    if cli_uri or cli_db:
        uri = cli_uri or get_env("MONGO_URI")
        if not uri:
            uri = "mongodb://localhost:27017/ytscan"

        # Determine DB name
        if cli_db:
            db_name = cli_db
        else:
            tail = uri.rsplit("/", 1)[-1]
            db_name = tail.split("?", 1)[0] or get_env("MONGO_DB") or "ytscan"

        client = MongoClient(uri)
        return client, client[db_name]

    # Case 2: Default project DB via config.db
    db = get_db_from_config()  # returns a Database object
    client = db.client
    return client, db


# -------------------------
# worker_runs logger
# -------------------------

def log_worker_run(worker_name: str, extra: dict | None = None) -> None:
    """
    Upsert a single document into `worker_runs` to record the last
    successful run (or error) of this worker.

    This is used by dashboards / health checks to monitor the system.
    """
    try:
        load_env()
        db = get_db_from_config()
        now_ts = now_utc()

        payload = {"name": worker_name, "last_run": now_ts}
        if extra:
            payload.update(extra)

        db.worker_runs.update_one(
            {"name": worker_name},
            {"$set": payload},
            upsert=True,
        )

    except Exception as e:
        print(f"[WARN] log_worker_run failed: {e}", file=sys.stderr)


# =========================
# Final logic
# =========================

# Values of ml_flags.viral_v2.final.status that mean "no more changes"
TERMINAL_FINAL_STATUSES = {
    "viral",
    "weak_viral",
    "super_viral",
    "non_viral",
    "non_viral_lowq",
    # Removed-aware terminal types
    "viral_after_removed",
    "removed",
}


def _get_stage_bool(
    stage: Dict[str, Any],
    default_thr_proba: float,
    default_thr_100: int,
    env_thr_proba_name: Optional[str] = None,
    env_thr_100_name: Optional[str] = None,
) -> Tuple[bool, float, int, float, int]:
    """
    Given a stage dict like ml_flags.viral_v2.h6 / h12 / h24,
    determine whether the stage considers the video "viral" (i.e., above
    its own threshold), and return:

        (is_over_threshold, threshold_proba, threshold_100, score_proba, score_100)

    Threshold resolution:
      1) Start from hard-coded defaults (e.g. 0.60 / 60 for 6h).
      2) If env vars are provided (VIRAL_V2_THRESH_*), override defaults.
      3) Stage-specific threshold fields (threshold_proba / threshold_100)
         override everything else.
    """
    # Apply env overrides if present
    if env_thr_proba_name:
        default_thr_proba = float(get_env(env_thr_proba_name, default_thr_proba))
    if env_thr_100_name:
        default_thr_100 = int(get_env(env_thr_100_name, default_thr_100))

    thr_p = float(stage.get("threshold_proba", default_thr_proba))
    thr_100 = int(stage.get("threshold_100", default_thr_100))

    score_p = float(stage.get("score_proba") or 0.0)
    score_100 = int(stage.get("score_100") or 0)

    is_over = (score_p >= thr_p) or (score_100 >= thr_100)
    return is_over, thr_p, thr_100, score_p, score_100


def _pick_label_from_top_class(
    is_over_threshold: bool,
    top_class: Optional[str],
) -> Optional[str]:
    """
    Map the per-stage multiclass top_class into a final label for that
    stage, if the stage is over its threshold.

    - If not over threshold → return None (stage does not vote viral).
    - If over threshold and top_class is one of:
        * "super_viral" → "super_viral"
        * "viral"       → "viral"
        * "weak_viral"  → "weak_viral"
        * "non_viral"   → "viral"  (rare, but treat as generic viral)
    - If top_class is missing/unknown → "viral" (fallback to legacy behavior).
    """
    if not is_over_threshold:
        return None

    if not top_class:
        return "viral"

    top_class = top_class.lower()
    if top_class == "super_viral":
        return "super_viral"
    if top_class == "viral":
        return "viral"
    if top_class == "weak_viral":
        return "weak_viral"
    if top_class == "non_viral":
        # This situation is logically odd (non_viral label but viral probability
        # above threshold). To avoid losing recall, treat as generic "viral".
        return "viral"

    # Unknown label → degrade gracefully to "viral"
    return "viral"


def build_final_from_ml(ml_v2: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Build the final decision document (fields under ml_flags.viral_v2.final.*)
    based on h6 / h12 / h24 scores + multiclass labels.

    Priority (if at least one stage passes its threshold):

        1) 24h validator → label from its top_class (super_viral / viral / weak_viral / ...),
                           reason="*_by_24h_validator"
        2) 12h confirmation → label from its top_class,
                              reason="*_by_12h_confirmation"
        3) 6h early signal → label from its top_class,
                             reason="*_by_6h_early_signal"

    If no stage passes its threshold:
        → status = "non_viral"
        → decided_stage = "24h"
        → reason = "all_stages_below_threshold"

    Returns:
        A dict with keys suitable for use in a `$set` update, or None
        if there is no useful h6/h12/h24 data at all.
    """
    h6 = ml_v2.get("h6") or {}
    h12 = ml_v2.get("h12") or {}
    h24 = ml_v2.get("h24") or ml_v2.get("h24_validation") or {}

    if not (h6 or h12 or h24):
        return None

    # --- Stage-level thresholds & scores ---
    over6, thr6_p, thr6_100, s6_p, s6_100 = _get_stage_bool(
        h6, 0.60, 60,
        "VIRAL_V2_THRESH_6H_PROBA", "VIRAL_V2_THRESH_6H_100"
    )
    over12, thr12_p, thr12_100, s12_p, s12_100 = _get_stage_bool(
        h12, 0.70, 70,
        "VIRAL_V2_THRESH_12H_PROBA", "VIRAL_V2_THRESH_12H_100"
    )
    over24, thr24_p, thr24_100, s24_p, s24_100 = _get_stage_bool(
        h24, 0.80, 80,
        "VIRAL_V2_THRESH_24H_PROBA", "VIRAL_V2_THRESH_24H_100"
    )

    # --- Stage-level top_class (from multiclass) ---
    top6 = (h6.get("top_class") or "").lower() or None
    top12 = (h12.get("top_class") or "").lower() or None
    top24 = (h24.get("top_class") or "").lower() or None

    # Compute stage-specific candidate labels (or None if below threshold)
    label_6 = _pick_label_from_top_class(over6, top6)
    label_12 = _pick_label_from_top_class(over12, top12)
    label_24 = _pick_label_from_top_class(over24, top24)

    # ---- Final trajectory metadata (for debugging / dashboards) ----
    now_iso = now_utc().isoformat()

    base_meta: Dict[str, Any] = {
        "ml_flags.viral_v2.final.top_class_6h": top6,
        "ml_flags.viral_v2.final.top_class_12h": top12,
        "ml_flags.viral_v2.final.top_class_24h": top24,
        "ml_flags.viral_v2.final.score_proba_6h": s6_p,
        "ml_flags.viral_v2.final.score_proba_12h": s12_p,
        "ml_flags.viral_v2.final.score_proba_24h": s24_p,
        "ml_flags.viral_v2.final.decided_at": now_iso,
    }

    # ---- Choose final status by stage priority ----
    # Stage priority: 24h → 12h → 6h
    if label_24 is not None:
        status = label_24
        decided_stage = "24h"
        score_p, score_100 = s24_p, s24_100
        thr_p, thr_100 = thr24_p, thr24_100
        reason = f"{status}_by_24h_validator"

    elif label_12 is not None:
        status = label_12
        decided_stage = "12h"
        score_p, score_100 = s12_p, s12_100
        thr_p, thr_100 = thr12_p, thr12_100
        reason = f"{status}_by_12h_confirmation"

    elif label_6 is not None:
        status = label_6
        decided_stage = "6h"
        score_p, score_100 = s6_p, s6_100
        thr_p, thr_100 = thr6_p, thr6_100
        reason = f"{status}_by_6h_early_signal"

    else:
        # No stage over threshold → final non_viral (with 24h metrics as baseline)
        status = "non_viral"
        decided_stage = "24h"
        score_p, score_100 = s24_p, s24_100
        thr_p, thr_100 = thr24_p, thr24_100
        reason = "all_stages_below_threshold"

    final_fields: Dict[str, Any] = {
        "ml_flags.viral_v2.final.status": status,
        "ml_flags.viral_v2.final.decided_stage": decided_stage,
        "ml_flags.viral_v2.final.score_proba": score_p,
        "ml_flags.viral_v2.final.score_100": score_100,
        "ml_flags.viral_v2.final.threshold_proba": thr_p,
        "ml_flags.viral_v2.final.threshold_100": thr_100,
        "ml_flags.viral_v2.final.reason": reason,
    }
    final_fields.update(base_meta)

    return final_fields


# =========================
# Argument parser
# =========================

def parse_args(argv=None) -> argparse.Namespace:
    """
    Parse CLI arguments for viral_finalize.

    Key options:
        --mongo-uri        : override Mongo URI (otherwise config.db is used)
        --db               : override DB name
        --collection       : videos collection name (default: env MONGO_COL_VIDEOS or 'videos')
        --min-age-hours    : minimum video age before finalization (default: 24h)
        --only-missing     : only finalize when final.status is missing/unknown
        --limit            : optional limit on number of documents to process
    """
    load_env()

    mongo_uri_default = get_env("MONGO_URI")
    db_default = get_env("MONGO_DB")

    parser = argparse.ArgumentParser(
        description="Finalize ml_flags.viral_v2.final.* based on h6/h12/h24 scores (multiclass-aware)."
    )
    parser.add_argument("--mongo-uri", default=mongo_uri_default)
    parser.add_argument("--db", default=db_default)
    parser.add_argument(
        "--collection",
        default=get_env("MONGO_COL_VIDEOS", "videos"),
        help="MongoDB collection containing video documents.",
    )
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=24.0,
        help="Minimum video age (in hours) before finalizing viral_v2.final (default: 24h).",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Only finalize videos where final.status is missing or 'unknown'.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on number of videos to process (debug / safety).",
    )
    return parser.parse_args(argv)


# =========================
# Main worker
# =========================

def main(argv=None) -> int:
    """
    Main worker entrypoint.

    High-level logic:
      1. Resolve DB / collection from CLI or project config.
      2. Select candidates:
           - must have stats_snapshots
           - must have ml_flags.viral_v2
           - if --only-missing: skip docs that already have a terminal final.status
      3. For each candidate:
           a) If blocked by low-quality → mark as non_viral_lowq.
           b) If video is too young (< min_age_hours) → skip,
              EXCEPT when tracking.status=complete & stop_reason=removed.
           c) Otherwise, derive final.* from h6/h12/h24 and write it back.
              If video is removed, wrap status into viral_after_removed / removed.
      4. Record stats in worker_runs for monitoring.
    """
    args = parse_args(argv)

    client, db = resolve_db(args.mongo_uri, args.db)
    coll = db[args.collection]

    now = now_utc()
    processed = 0
    finalized = 0
    skipped_age = 0
    skipped_no_ml = 0
    skipped_terminal = 0

    query: Dict[str, Any] = {
        "stats_snapshots.0": {"$exists": True},
        "ml_flags.viral_v2": {"$exists": True},
    }

    if args.only_missing:
        # Only consider docs where final is absent or status unknown / None
        query["$or"] = [
            {"ml_flags.viral_v2.final": {"$exists": False}},
            {"ml_flags.viral_v2.final.status": {"$in": [None, "unknown"]}},
        ]

    projection = {
        "_id": 1,
        "snippet": 1,
        "publishedAt": 1,
        "ml_flags": 1,
        "tracking": 1,
    }

    cursor = coll.find(query, projection=projection)

    if args.limit:
        cursor = cursor.limit(args.limit)

    print(
        f"[CFG] viral_finalize: coll={args.collection}, "
        f"min_age_hours={args.min_age_hours}, only_missing={args.only_missing}, "
        f"limit={args.limit}"
    )

    # -------- Loop videos ----------
    for doc in cursor:
        processed += 1
        vid = doc["_id"]

        ml_flags = doc.get("ml_flags") or {}
        ml_v2 = ml_flags.get("viral_v2") or {}
        final_info = ml_v2.get("final") or {}
        final_status = (final_info.get("status") or "unknown").lower()

        tracking = doc.get("tracking") or {}
        tracking_status = (tracking.get("status") or "").lower() or None
        stop_reason = (tracking.get("stop_reason") or "").lower() or None
        is_removed = stop_reason == "removed"

        # A. LOW-QUALITY BLOCK:
        lq_flag = False
        if stop_reason in {"low_quality", "ml.low_quality_v1_3h", "ml.low_quality_v3_6h"}:
            lq_flag = True
        else:
            # Fallback: check ml_flags.low_quality_* directly
            lq3 = (ml_flags.get("low_quality_v1_3h") or {})
            lq6 = (ml_flags.get("low_quality_v3_6h") or {})
            if lq3.get("is_low") or lq6.get("is_low"):
                lq_flag = True

        if lq_flag:
            update_fields = {
                "ml_flags.viral_v2.final.status": "non_viral_lowq",
                "ml_flags.viral_v2.final.decided_stage": "low_quality",
                "ml_flags.viral_v2.final.score_proba": None,
                "ml_flags.viral_v2.final.score_100": None,
                "ml_flags.viral_v2.final.threshold_proba": None,
                "ml_flags.viral_v2.final.threshold_100": None,
                "ml_flags.viral_v2.final.decided_at": now.isoformat(),
                "ml_flags.viral_v2.final.reason": "low_quality_block",
                "ml_flags.viral_v2.final.tracking_status_at_final": tracking_status,
                "ml_flags.viral_v2.final.tracking_stop_reason_at_final": stop_reason,
                "ml_flags.viral_v2.final.is_removed_at_final": bool(is_removed),
            }
            coll.update_one({"_id": vid}, {"$set": update_fields})
            finalized += 1
            continue

        # B. Terminal skip:
        if not args.only_missing and final_status in TERMINAL_FINAL_STATUSES:
            skipped_terminal += 1
            continue
        if args.only_missing and final_status not in (None, "unknown"):
            skipped_terminal += 1
            continue

        # C. Age check with exception for complete+removed
        pub_str = (doc.get("snippet") or {}).get("publishedAt") or doc.get("publishedAt")
        pub_dt = parse_iso8601(pub_str)
        if not pub_dt:
            skipped_no_ml += 1
            continue

        age_hours = (now - pub_dt).total_seconds() / 3600.0
        if age_hours < args.min_age_hours:
            # Allow early finalization for removed videos
            if not (tracking_status == "complete" and is_removed):
                skipped_age += 1
                continue

        # D. Build final decision based on h6/h12/h24 (multiclass-aware)
        update_fields = build_final_from_ml(ml_v2)

        # If we have absolutely no ML info (no h6/h12/h24 at all)
        if not update_fields:
            if is_removed:
                # Removed without ML evidence → mark as 'removed'
                update_fields = {
                    "ml_flags.viral_v2.final.status": "removed",
                    "ml_flags.viral_v2.final.decided_stage": "removed",
                    "ml_flags.viral_v2.final.score_proba": None,
                    "ml_flags.viral_v2.final.score_100": None,
                    "ml_flags.viral_v2.final.threshold_proba": None,
                    "ml_flags.viral_v2.final.threshold_100": None,
                    "ml_flags.viral_v2.final.decided_at": now.isoformat(),
                    "ml_flags.viral_v2.final.reason": "removed_before_ml",
                }
            else:
                # Fail-safe: video đủ tuổi nhưng không có bất kỳ score h6/h12/h24 nào
                skipped_no_ml += 1
                update_fields = {
                    "ml_flags.viral_v2.final.status": "unknown",
                    "ml_flags.viral_v2.final.decided_stage": "no_ml",
                    "ml_flags.viral_v2.final.score_proba": None,
                    "ml_flags.viral_v2.final.score_100": None,
                    "ml_flags.viral_v2.final.threshold_proba": None,
                    "ml_flags.viral_v2.final.threshold_100": None,
                    "ml_flags.viral_v2.final.decided_at": now.isoformat(),
                    "ml_flags.viral_v2.final.reason": "no_ml_scores",
                }

        # If video is removed and we do have an ML-based label:
        # wrap the base label into viral_after_removed / removed
        base_status = (update_fields.get("ml_flags.viral_v2.final.status") or "").lower()
        if tracking_status == "complete" and is_removed:
            # Preserve original status for training / analytics
            update_fields["ml_flags.viral_v2.final.base_status"] = base_status

            if base_status in {"viral", "weak_viral", "super_viral"}:
                update_fields["ml_flags.viral_v2.final.status"] = "viral_after_removed"
            else:
                update_fields["ml_flags.viral_v2.final.status"] = "removed"

            # Overwrite reason to make it explicit
            update_fields["ml_flags.viral_v2.final.reason"] = "removed"

        # Attach tracking-state metadata so we always know what happened
        update_fields.setdefault(
            "ml_flags.viral_v2.final.tracking_status_at_final", tracking_status
        )
        update_fields.setdefault(
            "ml_flags.viral_v2.final.tracking_stop_reason_at_final", stop_reason
        )
        update_fields.setdefault(
            "ml_flags.viral_v2.final.is_removed_at_final", bool(is_removed)
        )

        res = coll.update_one({"_id": vid}, {"$set": update_fields})
        if res.modified_count:
            finalized += 1

        if processed % 50 == 0:
            print(
                f"[viral_finalize] processed={processed}, finalized={finalized}, "
                f"skipped_age={skipped_age}, skipped_no_ml={skipped_no_ml}, "
                f"skipped_terminal={skipped_terminal}"
            )

    print(
        f"[DONE viral_finalize] processed={processed}, finalized={finalized}, "
        f"skipped_age={skipped_age}, skipped_no_ml={skipped_no_ml}, "
        f"skipped_terminal={skipped_terminal}"
    )

    log_worker_run(
        "viral_finalize",
        {
            "status": "ok",
            "processed": processed,
            "finalized": finalized,
            "skipped_age": skipped_age,
            "skipped_no_ml": skipped_no_ml,
            "skipped_terminal": skipped_terminal,
            "min_age_hours": args.min_age_hours,
        },
    )

    client.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
