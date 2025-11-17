# config/db.py
from __future__ import annotations

import os
from pymongo import MongoClient

try:
    from dotenv import load_dotenv
except Exception:
    # In case python-dotenv is not installed, this becomes a no-op.
    def load_dotenv(dotenv_path=None):
        return False


def get_db():
    """
    Return Mongo database using MONGO_URI + MONGO_DB from .env

    Expected env vars:
      - MONGO_URI (default: mongodb://127.0.0.1:27017)
      - MONGO_DB  (default: yt_autoscanner)
    """
    # Make sure .env is loaded (idempotent)
    load_dotenv()

    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    db_name = os.getenv("MONGO_DB", "yt_autoscanner")

    client = MongoClient(mongo_uri)
    return client[db_name]
