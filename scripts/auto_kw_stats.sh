#!/bin/bash
# Auto keyword_stats worker (incremental loop)
# Author: QuanBui AI

set -e

# =============================
# Config
# =============================
PROJECT_ROOT="/home/ytscan/yt-autoscanner"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/kw_stats.log"
SLEEP_SEC=30   # sleep between runs (seconds)

# =============================
# Prepare
# =============================
mkdir -p "$LOG_DIR"
cd "$PROJECT_ROOT"

echo "----------------------------------------" | tee -a "$LOG_FILE"
echo "Starting auto_kw_stats.sh (incremental)" | tee -a "$LOG_FILE"
echo "Command: $PYTHON_BIN -m worker.keyword_stats" | tee -a "$LOG_FILE"
echo "----------------------------------------" | tee -a "$LOG_FILE"

# =============================
# Main loop
# =============================
while true; do
    echo "[`date`] Running keyword_stats (incremental)..." | tee -a "$LOG_FILE"

    "$PYTHON_BIN" -m worker.keyword_stats \
        --mode incremental \
        --limit 500 \
        >> "$LOG_FILE" 2>&1

    echo "[`date`] Done. Sleeping ${SLEEP_SEC}s..." | tee -a "$LOG_FILE"
    sleep "$SLEEP_SEC"
done
