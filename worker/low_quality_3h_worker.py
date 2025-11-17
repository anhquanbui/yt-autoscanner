#!/usr/bin/env python3
"""
low_quality_3h_worker.py

Thin wrapper to run the low-quality worker in 3h-only mode:
- Chỉ chấm các video có age trong [3h, 6h)
- Dùng chung core logic từ low_quality_core.py
"""

from .low_quality_core import main as core_main


if __name__ == "__main__":
    import sys

    # Giữ nguyên mọi argument khác, nhưng ép --mode 3h-only
    argv = ["--mode", "3h-only", *sys.argv[1:]]
    core_main(argv)
