# config/env.py
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

_ENV_LOADED = False
_ENV_SOURCE: Optional[Path] = None


def _find_env_file() -> Optional[Path]:
    """
    Search for .env in priority:

      1. Parent folder of project root      (../.env)
      2. Project root                       (./.env)
      3. ANY subdirectory at ANY depth      (recursive)

    Returns Path if found.
    """

    here = Path(__file__).resolve()

    # config/env.py → parents[1] = project root
    project_root = here.parents[1]
    parent_folder = project_root.parent

    # -------- Priority list ----------
    candidates: list[Path] = []

    # 1️⃣ Parent folder
    candidates.append(parent_folder / ".env")

    # 2️⃣ Project root
    candidates.append(project_root / ".env")

    # 3️⃣ Recursive search inside project root
    try:
        for p in project_root.rglob(".env"):
            candidates.append(p)
    except Exception:
        pass

    # Return the first existing file
    for p in candidates:
        if p.is_file():
            return p

    return None


def load_env(force: bool = False) -> Optional[Path]:
    """Load .env once with priority search."""
    global _ENV_LOADED, _ENV_SOURCE

    if _ENV_LOADED and not force:
        return _ENV_SOURCE

    env_path = _find_env_file()

    if env_path:
        try:
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
    """Safe wrapper around os.getenv with auto-loading."""
    if not _ENV_LOADED:
        load_env()
    return os.getenv(name, default)
