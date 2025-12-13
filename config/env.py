# config/env.py
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Internal state: track whether .env has already been loaded
_ENV_LOADED = False
_ENV_SOURCE: Optional[Path] = None


def _find_env_file() -> Optional[Path]:
    """
    Locate a .env file using project-wide priority search.

    Priority (highest → lowest):
    1) Parent of project root   (../.env)
    2) Project root             (./.env)
    3) Any .env under project root (recursive)

    Returns the first .env file found, or None if none exists.
    """
    here = Path(__file__).resolve()

    # config/env.py → parents[1] = project root
    project_root = here.parents[1]
    parent_folder = project_root.parent

    candidates: list[Path] = []

    # 1) Parent folder of project root
    candidates.append(parent_folder / ".env")

    # 2) Project root
    candidates.append(project_root / ".env")

    # 3) Recursive search under project root
    try:
        for p in project_root.rglob(".env"):
            candidates.append(p)
    except Exception:
        # Ignore filesystem errors
        pass

    for p in candidates:
        if p.is_file():
            return p

    return None


def load_env(force: bool = False) -> Optional[Path]:
    """
    Load .env once using `_find_env_file()`.

    - Does not override existing system environment variables.
    - Safe to call multiple times (idempotent).
    - Can be forced to reload via force=True.

    Returns the loaded .env path, or None if none was found.
    """
    global _ENV_LOADED, _ENV_SOURCE

    if _ENV_LOADED and not force:
        return _ENV_SOURCE

    env_path = _find_env_file()

    if env_path:
        try:
            # override=False → keep existing system env vars
            load_dotenv(env_path, override=False)
            print(f"[INFO] .env loaded from: {env_path}")
            _ENV_LOADED = True
            _ENV_SOURCE = env_path
        except Exception as e:
            print(
                f"[WARN] Failed to load .env at {env_path}: {e}",
                file=sys.stderr,
            )
            _ENV_LOADED = True
            _ENV_SOURCE = None
    else:
        print("[INFO] No .env found. Using system environment.")
        _ENV_LOADED = True
        _ENV_SOURCE = None

    return _ENV_SOURCE


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Read an environment variable with automatic .env loading.

    - Ensures load_env() has run.
    - Returns env value if present, otherwise the provided default.
    """
    if not _ENV_LOADED:
        load_env()
    return os.getenv(name, default)
