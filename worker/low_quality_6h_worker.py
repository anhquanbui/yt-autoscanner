#!/usr/bin/env python3
"""
low_quality_6h_worker.py

Dedicated wrapper for running the low-quality ML pipeline in **6h-only** mode.

Behavior:
- Forces mode = "6h-only" (evaluate videos with age >= 6h).
- Forces --only-missing so we never re-score videos that already have a 6h score.
- Delegates all real compute work to low_quality_core.main(argv).
- Adds a clear [6H] log prefix making journalctl logs easier to search.
"""

from __future__ import annotations

from .low_quality_core import main as core_main


def _prepend_6h_log_tag(argv: list[str]) -> list[str]:
    """
    Emit an easy-to-spot log line at worker start.
    Makes journalctl/docker logs easier to follow.
    """
    print("[6H] low_quality_6h_worker starting (forced mode=6h-only, only-missing=True)")
    return argv


if __name__ == "__main__":
    import sys

    # Force:
    #   --mode 6h-only     → process only videos age >= 6h
    #   --only-missing     → skip videos already scored at 6h stage
    #
    # Pass through extra args from CLI so you can still use flags like:
    #   --limit N, --dry-run, etc. (if supported by low_quality_core)
    base_argv: list[str] = ["--mode", "6h-only", "--only-missing", *sys.argv[1:]]

    # Add tag/log line
    argv_with_tag = _prepend_6h_log_tag(base_argv)

    # Delegate to shared core pipeline
    core_main(argv_with_tag)
