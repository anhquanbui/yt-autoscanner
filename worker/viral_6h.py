# -*- coding: utf-8 -*-
"""
worker.viral_6h

Worker mỏng chạy mô hình viral 6h (ml_flags.viral_v2.h6)
ở chế độ only-missing mặc định.
"""

from .viral_prediction_core import (
    DEFAULT_MONGO_URI,
    DEFAULT_DB_NAME,
    DEFAULT_COLLECTION,
    MODEL_6H_PATH,
    HARDER_FEATURES_6H,
    build_features_6h,
    run_stage,
)


def main(argv=None):
    run_stage(
        stage="h6",
        model_path=MODEL_6H_PATH,
        feature_cols=HARDER_FEATURES_6H,
        build_features_fn=lambda rec, agg: build_features_6h(agg),
        mongo_uri=DEFAULT_MONGO_URI,
        db_name=DEFAULT_DB_NAME,
        coll_name=DEFAULT_COLLECTION,
        only_missing=True,   # luôn chạy chế độ only-missing
        force_all=False,
        limit=None,
        worker_runs_name="viral_scoring_h6",
        worker_status="ok", 
    )


if __name__ == "__main__":
    main()
