# config/path_utils.py
"""
Central helper for export/output directory resolution.

This version is fully aligned with config.env for unified .env loading.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from config.env import load_env, get_env


def get_export_dir(default_subdir: str = "data_export") -> Path:
    """
    Resolve the unified export/output directory for the project.

    Priority order:
      1. EXPORT_DIR    (if defined)
      2. OUTPUT_DIR    (fallback)
      3. <project_root>/<default_subdir>

    The directory is created if missing.
    """
    # Ensure .env is loaded once (idempotent)
    load_env()

    base = get_env("EXPORT_DIR") or get_env("OUTPUT_DIR")

    if base:
        export_path = Path(base).expanduser().resolve()
    else:
        # project_root = parent of config/
        project_root = Path(__file__).resolve().parents[1]
        export_path = project_root / default_subdir

    export_path.mkdir(parents=True, exist_ok=True)
    return export_path
