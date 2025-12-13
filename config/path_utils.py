# config/path_utils.py
"""
Central helper for resolving export/output directories.

Provides a single, environment-aware way to determine where
scripts, workers, and notebooks should write output files.

Aligned with config.env for consistent .env loading.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from config.env import load_env, get_env


def get_export_dir(default_subdir: str = "data_export") -> Path:
    """
    Resolve the export/output directory for the project.

    Priority (highest → lowest):
    1) EXPORT_DIR   (explicit override)
    2) OUTPUT_DIR   (legacy fallback)
    3) <project_root>/<default_subdir>

    Behavior:
    - Ensures .env is loaded
    - Creates the directory if missing (mkdir -p)
    - Returns an absolute Path
    """
    # Ensure environment variables are loaded
    load_env()

    # Highest-priority environment overrides
    base = get_env("EXPORT_DIR") or get_env("OUTPUT_DIR")

    if base:
        export_path = Path(base).expanduser().resolve()
    else:
        # config/path_utils.py → parents[1] = project root
        project_root = Path(__file__).resolve().parents[1]
        export_path = project_root / default_subdir

    # Ensure directory exists
    export_path.mkdir(parents=True, exist_ok=True)

    return export_path
