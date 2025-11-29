# -*- coding: utf-8 -*-
"""
worker.viral_24h

Worker mỏng chạy mô hình viral 24h validator
(ml_flags.viral_v2.h24_validation) ở chế độ only-missing.
"""

from .viral_prediction_core import (
    DEFAULT_MONGO_URI,
    DEFAULT_DB_NAME,
    DEFAULT_COLLECTION,
    MODEL_24H_PATH,
    build_features_24h,
    run_stage,
)


def main(argv=None):
    # feature_cols=[]: sẽ được override bên trong run_stage
    # bằng init_24h_features_from_model(model)
    run_stage(
        stage="h24_validation",
        model_path=MODEL_24H_PATH,
        feature_cols=[],
        build_features_fn=build_features_24h,
        mongo_uri=DEFAULT_MONGO_URI,
        db_name=DEFAULT_DB_NAME,
        coll_name=DEFAULT_COLLECTION,
        only_missing=True,
        force_all=False,
        limit=None,
        worker_runs_name="viral_scoring_h24",
        worker_status="ok",
    )


if __name__ == "__main__":
    main()
