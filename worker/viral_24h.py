# -*- coding: utf-8 -*-
"""
worker.viral_24h

Thin worker that runs the 24h viral validator model
(ml_flags.viral_v2.h24_validation) in "only-missing" mode by default.

Usage:
    python -m worker.viral_24h
"""

from __future__ import annotations

from .viral_prediction_core import (
    DEFAULT_MONGO_URI,
    DEFAULT_DB_NAME,
    DEFAULT_COLLECTION,
    MODEL_24H_PATH,
    run_stage,
)


def main(argv=None) -> None:
    """
    Entry point for the 24h validator worker.

    Notes
    -----
    - Always runs with:
        * stage_cmd="24h"
        * only_missing=True  → only score videos missing h24 score
        * force_all=False    → do not override existing h24 results
        * limit=None         → process all eligible items
    - Feature list and feature-building logic are handled internally
      by viral_prediction_core based on the model metadata.
    - Writes worker_runs document:
        * name  = "viral_scoring_h24"
        * status= "ok"
    """
    run_stage(
        stage_cmd="24h",
        model_path=MODEL_24H_PATH,
        mongo_uri=DEFAULT_MONGO_URI,
        db_name=DEFAULT_DB_NAME,
        coll_name=DEFAULT_COLLECTION,
        only_missing=True,
        force_all=False,
        limit=None,
        worker_runs_name="viral_scoring_h24",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
