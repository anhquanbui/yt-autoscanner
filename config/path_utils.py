# config/path_utils.py
"""
Central helper for export/output directory resolution.

This module provides a unified, environment-aware way to obtain
the default export/output directory for all scripts, workers,
and notebooks.

It is aligned with config.env for consistent .env loading.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from config.env import load_env, get_env


def get_export_dir(default_subdir: str = "data_export") -> Path:
    """
    Resolve the unified export/output directory for the project.

    Priority order (highest → lowest):
    ---------------------------------
    1. EXPORT_DIR (explicit override)
       - Allows full external control, e.g.:
         EXPORT_DIR=/mnt/drive/yt_outputs

    2. OUTPUT_DIR (legacy fallback)
       - Older scripts may still rely on OUTPUT_DIR.

    3. <project_root>/<default_subdir>
       - Standard: <repo_root>/data_export
       - Ensures safe default for local development and testing.

    Behavior:
    ---------
    - Ensures .env is loaded (via load_env())
    - Automatically creates the directory (mkdir -p)
    - Returns a resolved absolute Path

    Parameters:
    -----------
    default_subdir : str
        Folder used under project_root when no env overrides exist.
        Default is "data_export".

    Returns:
    --------
    Path
        Absolute path to the export directory.
    """
    # Ensure environment variables are loaded
    load_env()

    # Highest-priority environment variables
    base = get_env("EXPORT_DIR") or get_env("OUTPUT_DIR")

    if base:
        export_path = Path(base).expanduser().resolve()
    else:
        # project_root = parent of config/
        #   config/path_utils.py → parents[1] = <project_root>
        project_root = Path(__file__).resolve().parents[1]
        export_path = project_root / default_subdir

    # Ensure directory exists
    export_path.mkdir(parents=True, exist_ok=True)

    return export_path
