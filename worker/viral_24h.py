# -*- coding: utf-8 -*-
"""
worker.viral_24h

Thin worker that runs the 24h viral validator model
(ml_flags.viral_v2.h24_validation) in "only-missing" mode by default.

Intended usage:
    python -m worker.viral_24h

This module is a wrapper around `worker.viral_prediction_core.run_stage`,
making it easy for systemd / cron / shell scripts to trigger the 24h model
without requiring command-line arguments.
"""

from __future__ import annotations

from .viral_prediction_core import (
    DEFAULT_MONGO_URI,
    DEFAULT_DB_NAME,
    DEFAULT_COLLECTION,
    MODEL_24H_PATH,
    build_features_24h,
    run_stage,
)


def main(argv=None) -> None:
    """
    Entry point for the 24h validator worker.

    Notes
    -----
    - Always runs with:
        * stage="h24_validation"
        * only_missing=True  → only score videos missing h24 score
        * force_all=False    → do not override existing h24 results
        * limit=None         → no video limit, process all eligible items
    - `feature_cols=[]` is intentional:
      the actual feature list will be loaded dynamically from the model
      via `init_24h_features_from_model(model)` inside `run_stage`.
    - Writes worker_runs document:
        * name  = "viral_scoring_h24"
        * status= "ok"
    """
    run_stage(
        stage="h24_validation",
        model_path=MODEL_24H_PATH,
        feature_cols=[],                # Overridden inside run_stage (from model metadata)
        build_features_fn=build_features_24h,
        mongo_uri=DEFAULT_MONGO_URI,
        db_name=DEFAULT_DB_NAME,
        coll_name=DEFAULT_COLLECTION,
        only_missing=True,              # Only score items missing h24 score
        force_all=False,
        limit=None,
        worker_runs_name="viral_scoring_h24",
        worker_status="ok",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
