#!/usr/bin/env python3
"""
low_quality_6h_worker.py

Dedicated wrapper for running the low-quality ML pipeline in **6h-only** mode.

Behavior:
- Only evaluates videos whose age is >= 6 hours.
- Uses the shared core logic from low_quality_core.py.
- Automatically enables --only-missing to avoid re-scoring videos that already have 6h score.
"""

from .low_quality_core import main as core_main


def _prepend_6h_log_tag(argv: list[str]) -> list[str]:
    print("[6H] low_quality_6h_worker starting (forced mode = 6h-only, only-missing=True)")
    return argv


if __name__ == "__main__":
    import sys

    # Force mode = "6h-only"
    # Force --only-missing
    argv = ["--mode", "6h-only", "--only-missing", *sys.argv[1:]]

    # Add log tag
    argv = _prepend_6h_log_tag(argv)

    # Run the shared core pipeline
    core_main(argv)
