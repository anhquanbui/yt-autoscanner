#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ad_friendly_worker.py (v1, verbose / production-style)

What this worker does
---------------------
- Loads a trained "student" model from:
      models/ad_friendly/ad_friendly.joblib
  The model was trained to classify videos into:
      "AD_FRIENDLY" vs "NON_AD_FRIENDLY"
  using title + description (English-centric but robust enough in practice).

- For each eligible video in the `videos` collection, it:
    1) Builds a combined text:
           [TITLE] ... \n[DESC] ...
    2) Runs the model to get:
           - label  (AD_FRIENDLY / NON_AD_FRIENDLY)
           - score  (decision_function margin; larger |score| = more confident)
    3) Writes the result to:
        a) videos.ml_flags.ad_friendly_v1.*
            - label
            - score
            - updated_at (UTC)
        b) (Optionally) collection `ad_friendly`
            - for debug / analytics / retraining

- Each run also updates a single document in `worker_runs`:

    {
      name: "ad_friendly_v1",
      stage: "ad_friendly",
      last_run: <UTC datetime>,
      status: "ok" | "error",
      docs_scanned: <int>,
      docs_updated: <int>,
      error_message: <str or null>
    }

Command-line flags
------------------
- --only-missing
    Only process videos that do NOT yet have a label in
    ml_flags.ad_friendly_v1.label (or label is null).
- --limit N
    Process at most N videos in this run (for testing / throttling).
- --no-collection-log
    Do NOT write per-video logs into the `ad_friendly` collection,
    only update `videos.ml_flags.ad_friendly_v1.*`.

Examples
--------
    # Production-style: score any new/unlabeled videos
    python -m worker.ad_friendly_worker --only-missing

    # Same, but do NOT write to `ad_friendly` collection
    python -m worker.ad_friendly_worker --only-missing --no-collection-log

    # Test run on 1000 videos (whether missing or not)
    python -m worker.ad_friendly_worker --limit 1000
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict, Any

import joblib
from pymongo import MongoClient
from pymongo.collection import Collection

from config.env import load_env, get_env


# =====================================================================
#                        ENVIRONMENT & MONGO
# =====================================================================

def get_db():
    """Initialize MongoDB client + return db handle."""
    load_env()
    mongo_uri = get_env("MONGO_URI")
    db_name = get_env("MONGO_DB_NAME", "ytscan")

    client = MongoClient(mongo_uri)
    db = client[db_name]
    print(f"[ad_friendly] Connected to MongoDB, db = {db_name!r}")
    return db


db = get_db()
videos_col: Collection = db.videos
ad_friendly_col: Collection = db.ad_friendly
worker_runs_col: Collection = db.worker_runs


# =====================================================================
#                           MODEL LOADING
# =====================================================================

MODEL_PATH = "models/ad_friendly/ad_friendly.joblib"

print(f"[ad_friendly] Loading model from: {MODEL_PATH}")
payload: Dict[str, Any] = joblib.load(MODEL_PATH)

model = payload["model"]
model_info: Dict[str, Any] = payload.get("info", {}) or {}

MODEL_TYPE: str = model_info.get("type", "ad_friendly_lr_en")
TEACHER_MODEL: str = model_info.get("teacher_model", "omni-moderation-latest")
CLASSES: List[str] = list(model_info.get("classes", ["AD_FRIENDLY", "NON_AD_FRIENDLY"]))

print("[ad_friendly] Model loaded:")
print(f"  - type          : {MODEL_TYPE}")
print(f"  - teacher_model : {TEACHER_MODEL}")
print(f"  - classes       : {CLASSES}")


# =====================================================================
#                     PREDICTION UTILS (label + score)
# =====================================================================

def predict_label_and_score(text: str) -> Tuple[str, Optional[float]]:
    """
    Run the student model on a single combined text and return:

        label: 'AD_FRIENDLY' or 'NON_AD_FRIENDLY'
        score: decision_function margin (float) if available, else None

    For LinearSVC:
        - decision_function(x) returns a margin (distance to hyperplane).
        - Larger absolute value => more confident.

    We keep the raw margin to stay close to the underlying model behavior.
    """
    x = [text or ""]
    label = model.predict(x)[0]

    score: Optional[float] = None
    try:
        raw = model.decision_function(x)
        # raw can be shape (1,) or (1, n_classes) depending on implementation
        if hasattr(raw, "__len__") and len(raw) > 0:
            val = raw[0]
            # If val is an array (e.g. [margin]), take the first element
            if hasattr(val, "__len__"):
                val = val[0]
            score = float(val)
    except Exception as exc:  # noqa: BLE001
        # Some models may not expose decision_function; we degrade gracefully.
        print(f"[ad_friendly] WARN: decision_function failed ({exc!r}), score=None")
        score = None

    return label, score


# =====================================================================
#                         ARGUMENT PARSING
# =====================================================================

def parse_args(argv: List[str]) -> Dict[str, Any]:
    """
    Lightweight CLI args parser.

    Recognized flags:
        --only-missing
        --no-collection-log
        --limit N
    """
    cfg: Dict[str, Any] = {
        "only_missing": False,
        "log_collection": True,
        "limit": None,
    }

    if "--only-missing" in argv:
        cfg["only_missing"] = True

    if "--no-collection-log" in argv:
        cfg["log_collection"] = False

    if "--limit" in argv:
        try:
            idx = argv.index("--limit")
            cfg["limit"] = int(argv[idx + 1])
        except Exception:  # noqa: BLE001
            print("[ad_friendly] WARN: invalid or missing value after --limit, ignoring.")
            cfg["limit"] = None

    return cfg


# =====================================================================
#                     worker_runs helper (simple doc)
# =====================================================================

def update_run_status(
    status: str,
    docs_scanned: int,
    docs_updated: int,
    error_message: Optional[str] = None,
) -> None:
    """
    Upsert a single summary document in worker_runs for this worker.
    Style is similar to viral_scoring_h24, etc.
    """
    doc: Dict[str, Any] = {
        "name": "ad_friendly_v1",
        "stage": "ad_friendly",
        "last_run": datetime.now(timezone.utc),
        "status": status,
        "docs_scanned": docs_scanned,
        "docs_updated": docs_updated,
    }
    if error_message is not None:
        doc["error_message"] = error_message

    worker_runs_col.update_one(
        {"name": "ad_friendly_v1"},
        {"$set": doc},
        upsert=True,
    )


# =====================================================================
#                        MAIN WORKER FUNCTION
# =====================================================================

def main(argv: List[str]) -> int:
    from sys import argv as _argv  # only used to display entry

    cfg = parse_args(argv)
    only_missing: bool = cfg["only_missing"]
    log_collection: bool = cfg["log_collection"]
    limit: Optional[int] = cfg["limit"]

    print("[ad_friendly] ====================================================")
    print("[ad_friendly] Starting ad_friendly_worker")
    print("[ad_friendly] CLI argv:", _argv)
    print("[ad_friendly] Run config:")
    print(f"  - only_missing      : {only_missing}")
    print(f"  - log_collection    : {log_collection}")
    print(f"  - limit             : {limit}")
    print("[ad_friendly] ====================================================")

    # -------------------------------
    # Build MongoDB query
    # -------------------------------
    query: Dict[str, Any] = {
        "snippet.title": {"$type": "string"},
    }

    if only_missing:
        # Only videos where ad_friendly_v1 is missing or label is still null.
        query["$or"] = [
            {"ml_flags.ad_friendly_v1": {"$exists": False}},
            {"ml_flags.ad_friendly_v1.label": None},
        ]

    projection = {
        "_id": 1,
        "snippet.title": 1,
        "snippet.description": 1,
    }

    total_candidates = videos_col.count_documents(query)
    cursor = videos_col.find(query, projection)

    if limit is not None:
        cursor = cursor.limit(limit)

    print(f"[ad_friendly] Matching videos: {total_candidates} (limit={limit})")

    docs_scanned = 0
    docs_updated = 0

    try:
        for doc in cursor:
            docs_scanned += 1

            vid = doc["_id"]
            snip = doc.get("snippet", {}) or {}
            title = snip.get("title", "") or ""
            desc = snip.get("description", "") or ""

            combined_text = f"[TITLE] {title}\n[DESC] {desc}"

            label, score = predict_label_and_score(combined_text)
            now = datetime.now(timezone.utc)

            # --------------------------------------------------------
            # 1) Optional: write to dedicated `ad_friendly` collection
            # --------------------------------------------------------
            if log_collection:
                ad_friendly_col.update_one(
                    {"_id": vid},
                    {
                        "$set": {
                            "label": label,
                            "score": score,
                            "combined_text": combined_text,
                            "updated_at": now,
                            "source": "student_model_v1",
                            "model_type": MODEL_TYPE,
                            "teacher_model": TEACHER_MODEL,
                        }
                    },
                    upsert=True,
                )

            # --------------------------------------------------------
            # 2) Always write inline into videos.ml_flags.ad_friendly_v1
            # --------------------------------------------------------
            update_fields: Dict[str, Any] = {
                "ml_flags.ad_friendly_v1.label": label,
                "ml_flags.ad_friendly_v1.updated_at": now,
            }
            if score is not None:
                update_fields["ml_flags.ad_friendly_v1.score"] = score

            videos_col.update_one({"_id": vid}, {"$set": update_fields})
            docs_updated += 1

            if docs_scanned % 200 == 0:
                print(
                    f"[ad_friendly] Progress: scanned={docs_scanned}, "
                    f"updated={docs_updated}"
                )

        # ---------------------------
        # Mark run summary as OK
        # ---------------------------
        update_run_status("ok", docs_scanned, docs_updated)
        print(
            f"[ad_friendly] DONE. "
            f"scanned={docs_scanned}, updated={docs_updated}"
        )
        return 0

    except Exception as exc:  # noqa: BLE001
        # ---------------------------
        # Mark run summary as ERROR
        # ---------------------------
        error_msg = str(exc)
        update_run_status("error", docs_scanned, docs_updated, error_msg)
        print(
            f"[ad_friendly] ERROR: {exc!r} "
            f"(scanned={docs_scanned}, updated={docs_updated})"
        )
        # Re-raise so systemd / caller can see non-zero exit
        raise


if __name__ == "__main__":
    from sys import argv as _argv
    raise SystemExit(main(_argv[1:]))
