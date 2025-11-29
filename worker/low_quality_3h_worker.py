#!/usr/bin/env python3
"""
low_quality_3h_worker.py

Dedicated wrapper for running the low-quality ML pipeline in **3h-only** mode.

Behavior:
- Forces mode = "3h-only" (videos whose age is within [3h, 6h))
- Forces --only-missing so we do NOT re-score videos that already have 3h results
- Delegates all real work to low_quality_core.main(argv)
- Adds a clear log prefix "[3H]" so logs are easy to filter in journalctl

Intended usage (example):

    python -m worker.low_quality_3h_worker

This module is typically wired into a systemd service, e.g.:

    ExecStart=/path/to/venv/bin/python -m worker.low_quality_3h_worker
"""

from __future__ import annotations

from .low_quality_core import main as core_main


def _prepend_3h_log_tag(argv: list[str]) -> list[str]:
    """
    Add a one-time log line, so when this worker starts you can easily spot it
    in the logs (journalctl / docker logs, etc.).

    We simply print a message and then return the argv unchanged.
    """
    print("[3H] low_quality_3h_worker starting (forced mode=3h-only, only-missing=True)")
    return argv


if __name__ == "__main__":
    import sys

    # Base argv forcing:
    #   --mode 3h-only      → restrict core pipeline to [3h, 6h) age band
    #   --only-missing      → skip videos that already have 3h low-quality scores
    #
    # Note: we append any extra CLI args after these, so you can still pass
    # things like --limit, --dry-run, etc., if low_quality_core supports them.
    base_argv: list[str] = ["--mode", "3h-only", "--only-missing", *sys.argv[1:]]

    # Add startup log line
    argv_with_tag = _prepend_3h_log_tag(base_argv)

    # Delegate to shared core pipeline
    # low_quality_core.main(...) is expected to parse argv and exit accordingly.
    core_main(argv_with_tag)
