# -*- coding: utf-8 -*-
"""
worker.viral_6h

Thin worker that runs the 6h viral model (ml_flags.viral_v2.h6)
in "only-missing" mode by default.

Usage:
    python -m worker.viral_6h
"""

from __future__ import annotations

from .viral_prediction_core import (
    DEFAULT_MONGO_URI,
    DEFAULT_DB_NAME,
    DEFAULT_COLLECTION,
    MODEL_6H_PATH,
    run_stage,
)


def main(argv=None) -> None:
    """
    Entry point for the 6h viral scoring worker.

    Notes
    -----
    - Always runs with:
        * stage_cmd="6h"
        * only_missing=True  → only score videos missing h6.score_proba
        * force_all=False    → never overwrite existing scores
        * limit=None         → process all eligible videos
    - Mongo URI / DB name / collection name / model path
      are taken from viral_prediction_core.
    - Writes a worker_runs document:
        * name  = "viral_scoring_h6"
        * status= "ok"
    """
    run_stage(
        stage_cmd="6h",
        model_path=MODEL_6H_PATH,
        mongo_uri=DEFAULT_MONGO_URI,
        db_name=DEFAULT_DB_NAME,
        coll_name=DEFAULT_COLLECTION,
        only_missing=True,
        force_all=False,
        limit=None,
        worker_runs_name="viral_scoring_h6",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
