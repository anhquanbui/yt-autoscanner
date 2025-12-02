# -*- coding: utf-8 -*-
"""
worker.viral_12h

Thin worker that runs the 12h viral model (ml_flags.viral_v2.h12)
in "only-missing" mode by default.

Usage:
    python -m worker.viral_12h
"""

from __future__ import annotations

from .viral_prediction_core import (
    DEFAULT_MONGO_URI,
    DEFAULT_DB_NAME,
    DEFAULT_COLLECTION,
    MODEL_12H_PATH,
    run_stage,
)


def main(argv=None) -> None:
    """
    Entry point for the 12h viral scoring worker.

    Notes
    -----
    - Always runs with:
        * stage_cmd="12h"
        * only_missing=True  → only score videos missing h12.score_proba
        * force_all=False    → never recalc existing scores
        * limit=None         → process all eligible videos
    - Mongo URI / DB name / collection name / model path
      are inherited from viral_prediction_core.
    - Writes a worker_runs document:
        * name  = "viral_scoring_h12"
        * status= "ok"
    """
    run_stage(
        stage_cmd="12h",
        model_path=MODEL_12H_PATH,
        mongo_uri=DEFAULT_MONGO_URI,
        db_name=DEFAULT_DB_NAME,
        coll_name=DEFAULT_COLLECTION,
        only_missing=True,
        force_all=False,
        limit=None,
        worker_runs_name="viral_scoring_h12",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
