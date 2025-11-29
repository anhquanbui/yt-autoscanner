# -*- coding: utf-8 -*-
"""
worker.viral_12h

Worker mỏng chạy mô hình viral 12h (ml_flags.viral_v2.h12)
ở chế độ only-missing mặc định.
"""

from .viral_prediction_core import (
    DEFAULT_MONGO_URI,
    DEFAULT_DB_NAME,
    DEFAULT_COLLECTION,
    MODEL_12H_PATH,
    HARDER_FEATURES_12H,
    build_features_12h,
    run_stage,
)


def main(argv=None):
    run_stage(
        stage="h12",
        model_path=MODEL_12H_PATH,
        feature_cols=HARDER_FEATURES_12H,
        build_features_fn=lambda rec, agg: build_features_12h(agg),
        mongo_uri=DEFAULT_MONGO_URI,
        db_name=DEFAULT_DB_NAME,
        coll_name=DEFAULT_COLLECTION,
        only_missing=True,
        force_all=False,
        limit=None,
        worker_runs_name="viral_scoring_h12",
        worker_status="ok",
    )


if __name__ == "__main__":
    main()
