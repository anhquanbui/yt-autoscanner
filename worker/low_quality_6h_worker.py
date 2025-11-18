#!/usr/bin/env python3
"""
low_quality_6h_worker.py

Dedicated wrapper for running the low-quality ML pipeline in **6h-only** mode.

Behavior:
- Only evaluates videos whose age is >= 6 hours.
- Uses the shared core logic from low_quality_core.py.
- Ensures that logs printed by this worker are clearly marked as 6h-only model logs.
"""

from .low_quality_core import main as core_main


def _prepend_6h_log_tag(argv: list[str]) -> list[str]:
    """
    Insert a visible log tag for clarity so that all logs coming from this worker
    are clearly identifiable as 6h-only model logs.
    """
    print("[6H] low_quality_6h_worker starting (forced mode = 6h-only)")
    return argv


if __name__ == "__main__":
    import sys

    # Force mode = "6h-only" regardless of any CLI args.
    # All other CLI arguments are preserved.
    argv = ["--mode", "6h-only", *sys.argv[1:]]

    # Add 6h log tag
    argv = _prepend_6h_log_tag(argv)

    # Run the shared core pipeline
    core_main(argv)
