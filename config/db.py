# config/db.py
from __future__ import annotations

from typing import Optional
from pathlib import Path  # Currently unused, but kept in case future helpers need it.

from pymongo import MongoClient

from config.env import load_env, get_env


# Load environment variables once at import time.
# This follows the same pattern as the rest of the project:
#   - 1) ~/.env
#   - 2) <project_root>/.env
#   - 3) Nested .env files under the project tree
#
# Note: load_env() is idempotent, so calling it multiple times is safe.
load_env()


def _resolve_db_name(mongo_uri: str, explicit_db: Optional[str]) -> str:
    """
    Resolve the database name given a Mongo URI and an optional explicit override.

    Resolution priority
    -------------------
    1. explicit_db (typically from MONGO_DB or a CLI flag)
       - If explicitly provided, we trust it and return it as-is.
    2. Parsed DB name from the URI tail:
       - Example: mongodb://host:27017/ytscan?retryWrites=true
                  → "ytscan"
    3. Fallback to a hard-coded default:
       - "ytscan"

    Parameters
    ----------
    mongo_uri:
        Full Mongo connection string. May or may not contain a DB suffix.
    explicit_db:
        Optional database name that should override any URI suffix.

    Returns
    -------
    str
        The database name to use when creating a MongoClient database handle.
    """
    if explicit_db:
        # Highest priority: explicit override (e.g. MONGO_DB, CLI flag)
        return explicit_db

    # Try to parse the DB name from the URI suffix:
    #   mongodb://host:port/<db>?<options>
    tail = mongo_uri.rsplit("/", 1)[-1]
    candidate = tail.split("?", 1)[0]
    if candidate:
        return candidate

    # Final fallback if URI has no db component at all
    return "ytscan"


def get_db():
    """
    Return a MongoDB database handle using the shared environment loader.

    This helper centralizes MongoDB configuration so that all scripts and
    services use a consistent connection strategy.

    Environment variables
    ---------------------
    - MONGO_URI
        Full Mongo connection string.
        Example: "mongodb://127.0.0.1:27017/ytscan"
        Default if not set: "mongodb://127.0.0.1:27017/ytscan"

    - MONGO_DB
        Optional database name override. If present, it takes precedence
        over any db name embedded in MONGO_URI.

    Resolution logic
    ----------------
    1. Ensure environment variables are loaded via config.env.load_env().
       (This is idempotent and safe to call multiple times.)
    2. Read MONGO_URI, falling back to a local default.
    3. Read MONGO_DB (may be None).
    4. Compute the effective DB name via _resolve_db_name().
    5. Instantiate MongoClient(mongo_uri) and return client[db_name].

    Returns
    -------
    Database
        A `pymongo.database.Database` instance bound to the resolved DB name.

    Notes
    -----
    - Connection pooling is handled internally by MongoClient.
      Each call to get_db() returns a DB object from a new client,
      which is sufficient for scripts and short-lived CLI tools.
    - For long-lived web services or workers, you may want to create the
      client once at module import time and reuse it, but this helper keeps
      things simple and explicit for most use cases.
    """
    # Make sure env is loaded (idempotent, in case other modules import this late)
    load_env()

    # Connection string: prefer MONGO_URI from env, otherwise use a local default.
    mongo_uri = get_env("MONGO_URI", "mongodb://127.0.0.1:27017/ytscan")

    # Optional explicit DB override (MONGO_DB).
    db_name_env = get_env("MONGO_DB")

    # Decide which DB name to use (env override → URI tail → default).
    db_name = _resolve_db_name(mongo_uri, db_name_env)

    # Create a new client and return the requested database handle.
    client = MongoClient(mongo_uri)
    return client[db_name]
