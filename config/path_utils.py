#!/usr/bin/env python3
"""
path_utils.py — Central helpers for export/output directories.

Usage:
    from config.path_utils import get_export_dir
    export_dir = get_export_dir()
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    # Fallback no-op if python-dotenv is not installed
    def load_dotenv(dotenv_path=None):
        return False


def get_export_dir(default_subdir: str = "data_export") -> Path:
    """
    Resolve the unified export/output directory for the project.

    Priority order:
      1. Environment variable EXPORT_DIR
      2. Environment variable OUTPUT_DIR
      3. <project_root>/<default_subdir>
         (by default: yt-autoscanner/data_export)

    The directory is created if it does not exist.
    """
    # Load .env once (idempotent)
    load_dotenv()

    base = os.getenv("EXPORT_DIR") or os.getenv("OUTPUT_DIR")
    if base:
        export_path = Path(base).expanduser().resolve()
    else:
        # project_root = parent of config/ (i.e. yt-autoscanner/)
        project_root = Path(__file__).resolve().parents[1]
        export_path = project_root / default_subdir

    export_path.mkdir(parents=True, exist_ok=True)
    return export_path
