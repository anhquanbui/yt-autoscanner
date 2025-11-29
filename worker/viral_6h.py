# -*- coding: utf-8 -*-
"""
worker.viral_6h

Thin worker that runs the 6h viral model (ml_flags.viral_v2.h6)
in "only-missing" mode by default.

Intended usage:
    python -m worker.viral_6h

This module is a convenience wrapper around `worker.viral_prediction_core.run_stage`,
so that systemd / cron / bash scripts can call a single, fixed entrypoint
for the 6h model without having to pass CLI arguments.
"""

from __future__ import annotations

from .viral_prediction_core import (
    DEFAULT_MONGO_URI,
    DEFAULT_DB_NAME,
    DEFAULT_COLLECTION,
    MODEL_6H_PATH,
    HARDER_FEATURES_6H,
    build_features_6h,
    run_stage,
)


def main(argv=None) -> None:
    """
    Entry point for the 6h viral scoring worker.

    Notes
    -----
    - This worker always runs with:
        * stage="h6"
        * only_missing=True  → only score videos that do not yet have h6.score_proba
        * force_all=False    → never override existing scores
        * limit=None         → no hard cap on number of videos
    - MongoDB connection / DB / collection and model path are taken
      from the shared configuration in `viral_prediction_core`.
    - The worker_run document will be written with:
        * name  = "viral_scoring_h6"
        * status= "ok"
    """
    run_stage(
        stage="h6",                    # 6h early-signal stage → writes ml_flags.viral_v2.h6
        model_path=MODEL_6H_PATH,      # path to the 6h model (joblib), from shared config
        feature_cols=HARDER_FEATURES_6H,  # ordered list of feature names for the 6h model
        # build_features_fn receives (rec, agg). For 6h we only need the aggregated stats,
        # so we ignore `rec` and pass `agg` into build_features_6h.
        build_features_fn=lambda rec, agg: build_features_6h(agg),
        mongo_uri=DEFAULT_MONGO_URI,   # Mongo URI (taken from env, with sensible default)
        db_name=DEFAULT_DB_NAME,       # database name (usually "ytscan")
        coll_name=DEFAULT_COLLECTION,  # videos collection
        only_missing=True,             # only score documents that are missing h6.score_proba
        force_all=False,               # do not recompute existing scores
        limit=None,                    # no limit → process all matching candidates
        worker_runs_name="viral_scoring_h6",  # name used in worker_runs collection
        worker_status="ok",            # status flag for dashboards / health checks
    )


if __name__ == "__main__":  # pragma: no cover
    # When executed as a script (python -m worker.viral_6h),
    # run the worker once and exit with the returned status.
    main()
