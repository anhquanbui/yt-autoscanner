#!/usr/bin/env python3
"""
low_quality_3h_worker.py

Dedicated wrapper for running the low-quality ML pipeline in **3h-only** mode.

Behavior:
- Only evaluates videos whose age is within the range [3h, 6h)
- Uses the shared core logic from low_quality_core.py
- Ensures that logs printed by this worker are explicitly marked as "3h model"
- Automatically enables --only-missing to avoid re-scoring old videos
"""

from .low_quality_core import main as core_main


def _prepend_3h_log_tag(argv: list[str]) -> list[str]:
    print("[3H] low_quality_3h_worker starting (forced mode = 3h-only, only-missing=True)")
    return argv


if __name__ == "__main__":
    import sys

    # Force mode = "3h-only"
    # Force --only-missing
    argv = ["--mode", "3h-only", "--only-missing", *sys.argv[1:]]

    # Add 3h log tag
    argv = _prepend_3h_log_tag(argv)

    # Run the shared core pipeline
    core_main(argv)
