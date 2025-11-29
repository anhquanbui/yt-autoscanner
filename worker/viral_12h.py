# -*- coding: utf-8 -*-
"""
worker.viral_12h

Thin worker that runs the 12h viral model (ml_flags.viral_v2.h12)
in "only-missing" mode by default.

Intended usage:
    python -m worker.viral_12h

This worker is a simple wrapper around `worker.viral_prediction_core.run_stage`,
allowing systemd / cron / bash scripts to run the 12h model via a fixed,
argument-free entrypoint.
"""

from __future__ import annotations

from .viral_prediction_core import (
    DEFAULT_MONGO_URI,
    DEFAULT_DB_NAME,
    DEFAULT_COLLECTION,
    MODEL_12H_PATH,
    HARDER_FEATURES_12H,
    build_features_12h,
    run_stage,
)


def main(argv=None) -> None:
    """
    Entry point for the 12h viral scoring worker.

    Notes
    -----
    - Always runs with:
        * stage="h12"
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
        stage="h12",                        # Writes into ml_flags.viral_v2.h12
        model_path=MODEL_12H_PATH,          # Path to the 12h XGB model
        feature_cols=HARDER_FEATURES_12H,   # Ordered feature list (12h training spec)
        build_features_fn=lambda rec, agg: build_features_12h(agg),  # build row from agg
        mongo_uri=DEFAULT_MONGO_URI,
        db_name=DEFAULT_DB_NAME,
        coll_name=DEFAULT_COLLECTION,
        only_missing=True,   # do not overwrite existing h12 scores
        force_all=False,
        limit=None,
        worker_runs_name="viral_scoring_h12",
        worker_status="ok",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
