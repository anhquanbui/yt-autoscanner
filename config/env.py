# config/env.py
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Internal state — tracks whether we've already loaded .env
_ENV_LOADED = False
_ENV_SOURCE: Optional[Path] = None


def _find_env_file() -> Optional[Path]:
    """
    Locate a .env file using the project-wide priority search.

    Priority (highest → lowest)
    ---------------------------
    1. Parent folder of the project root      (../.env)
       - This allows sharing env settings between multiple project clones
         or grouping secrets outside the repo.

    2. Project root                           (./.env)
       - Standard placement: <project_root>/.env

    3. Recursive search under project root    (any subdir)
       - Useful for cases where the project is deployed in nested structures
         or the user accidentally placed .env deeper in the tree.

    Behavior
    --------
    Returns
        Path to the first .env file found in the priority scan.
        None if no .env file exists anywhere.

    Notes
    -----
    - Does not load the file; only locates it.
    - Robust to permission errors or broken symlinks.
    """
    here = Path(__file__).resolve()

    # Example:
    #   config/env.py → parents[1] = project root
    #   parents[0] is the 'config' folder
    project_root = here.parents[1]
    parent_folder = project_root.parent

    # Priority candidate list (ordered)
    candidates: list[Path] = []

    # 1️⃣ Parent folder of project root
    candidates.append(parent_folder / ".env")

    # 2️⃣ Project root
    candidates.append(project_root / ".env")

    # 3️⃣ Recursive search within project root
    try:
        for p in project_root.rglob(".env"):
            candidates.append(p)
    except Exception:
        # Ignore filesystem issues silently
        pass

    # Return the first existing .env file in the priority list
    for p in candidates:
        if p.is_file():
            return p

    return None


def load_env(force: bool = False) -> Optional[Path]:
    """
    Load a .env file once using the priority search from `_find_env_file()`.

    Parameters
    ----------
    force:
        If True, reload .env even if it was previously loaded.
        (Useful for testing or environments where settings change at runtime.)

    Behavior
    --------
    - Uses python-dotenv to load variables into the environment.
    - Does **not** override environment variables that are already defined.
    - Logs where .env was loaded from, or indicates fallback to system env.

    Returns
    -------
    Path | None
        The path of the loaded .env file, or None if no file was loaded.
    """
    global _ENV_LOADED, _ENV_SOURCE

    if _ENV_LOADED and not force:
        return _ENV_SOURCE

    env_path = _find_env_file()

    if env_path:
        try:
            # override=False means we do NOT clobber system env vars
            load_dotenv(env_path, override=False)
            print(f"[INFO] .env loaded from: {env_path}")
            _ENV_LOADED = True
            _ENV_SOURCE = env_path
        except Exception as e:
            print(f"[WARN] Failed to load .env at {env_path}: {e}", file=sys.stderr)
            _ENV_LOADED = True
            _ENV_SOURCE = None
    else:
        print("[INFO] No .env found. Using system environment.")
        _ENV_LOADED = True
        _ENV_SOURCE = None

    return _ENV_SOURCE


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Safe wrapper around os.getenv with automatic .env loading.

    Behavior
    --------
    - Ensures load_env() has been executed.
    - Returns the environment variable if present, otherwise the provided default.

    Parameters
    ----------
    name:
        Environment variable key.
    default:
        Value returned when `name` is not present in environment.

    Returns
    -------
    str | None
        The resolved environment variable or the default value.
    """
    if not _ENV_LOADED:
        load_env()
    return os.getenv(name, default)
