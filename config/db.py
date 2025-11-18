# config/db.py
from __future__ import annotations

from typing import Optional
from pathlib import Path

from pymongo import MongoClient

from config.env import load_env, get_env


# Ensure .env is loaded once for the whole process
load_env()


def _resolve_db_name(mongo_uri: str, explicit_db: Optional[str]) -> str:
    """
    Resolve the database name using the following priority:

      1. Explicit MONGO_DB from env (if set)
      2. Tail of MONGO_URI (mongodb://host:port/<db>?...)
      3. Fallback default: "ytscan"
    """
    if explicit_db:
        return explicit_db

    # Try to parse db name from the URI suffix
    tail = mongo_uri.rsplit("/", 1)[-1]
    candidate = tail.split("?", 1)[0]
    if candidate:
        return candidate

    return "ytscan"


def get_db():
    """
    Return a MongoDB database handle using shared env loader.

    Environment variables:

      - MONGO_URI : full Mongo connection string
                    default: "mongodb://127.0.0.1:27017/ytscan"
      - MONGO_DB  : (optional) database name override
                    if not set, we try to parse it from MONGO_URI,
                    and finally fallback to "ytscan".
    """
    # Make sure env is loaded (idempotent, safe to call again)
    load_env()

    mongo_uri = get_env("MONGO_URI", "mongodb://127.0.0.1:27017/ytscan")
    db_name_env = get_env("MONGO_DB")

    db_name = _resolve_db_name(mongo_uri, db_name_env)

    client = MongoClient(mongo_uri)
    return client[db_name]
