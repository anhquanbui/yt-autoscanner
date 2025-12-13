# config/db.py
from __future__ import annotations

from typing import Optional
from pathlib import Path  # Currently unused; kept for potential future helpers.

from pymongo import MongoClient

from config.env import load_env, get_env


# Load environment variables once at import time.
# The actual search order / strategy is implemented in config.env.load_env().
# Note: load_env() is expected to be idempotent, so calling it multiple times is safe.
load_env()


def _resolve_db_name(mongo_uri: str, explicit_db: Optional[str]) -> str:
    """
    Resolve the MongoDB database name from:
      1) explicit override (e.g., MONGO_DB), then
      2) URI suffix (e.g., ".../ytscan?retryWrites=true"), then
      3) default fallback ("ytscan").

    Parameters
    ----------
    mongo_uri:
        Full Mongo connection string (may or may not include a db suffix).
    explicit_db:
        Optional database name override (takes highest priority).

    Returns
    -------
    str
        The effective database name to use for client[db_name].
    """
    if explicit_db:
        # Highest priority: explicit override (e.g., MONGO_DB, CLI flag)
        return explicit_db

    # Attempt to parse a db name from the URI suffix:
    #   mongodb://host:port/<db>?<options>
    # Note: if the URI ends with "/<db>", rsplit("/", 1)[-1] returns "<db>".
    tail = mongo_uri.rsplit("/", 1)[-1]
    candidate = tail.split("?", 1)[0]
    if candidate:
        return candidate

    # Final fallback if URI has no db component at all
    return "ytscan"


def get_db():
    """
    Return a MongoDB database handle using environment configuration.

    Environment variables
    ---------------------
    MONGO_URI:
        Full Mongo connection string.
        Example: "mongodb://127.0.0.1:27017/ytscan"
        Default: "mongodb://127.0.0.1:27017/ytscan"

    MONGO_DB:
        Optional database name override. If set, it takes precedence over
        any db name embedded in MONGO_URI.

    Resolution logic
    ----------------
    1) Ensure environment variables are loaded via load_env() (idempotent).
    2) Read MONGO_URI (with a local default fallback).
    3) Read MONGO_DB (optional).
    4) Resolve the effective db name via _resolve_db_name().
    5) Create a MongoClient and return client[db_name].

    Returns
    -------
    Database
        A `pymongo.database.Database` instance bound to the resolved db name.

    Notes
    -----
    - MongoClient manages internal connection pooling.
    - This helper creates a new client per call, which is fine for scripts
      and short-lived tools.
    - For long-lived services (FastAPI/workers), you may prefer a singleton
      client reused across requests/process lifetime.
    """
    # Ensure env is loaded (idempotent; safe even if already loaded on import)
    load_env()

    # Prefer MONGO_URI from env; otherwise use a local default.
    mongo_uri = get_env("MONGO_URI", "mongodb://127.0.0.1:27017/ytscan")

    # Optional explicit DB override.
    db_name_env = get_env("MONGO_DB")

    # Resolve db name (explicit override → URI suffix → default).
    db_name = _resolve_db_name(mongo_uri, db_name_env)

    # Create a new client and return the requested database handle.
    client = MongoClient(mongo_uri)
    return client[db_name]
