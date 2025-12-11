#!/bin/bash
# Auto KW Stats (incremental mode)
# Loop xử lý keyword_stats dựa trên worker.keyword_stats
# Author: QuanBui AI

set -e

# -----------------------------
# CONFIG
# -----------------------------
PROJECT_ROOT="/home/ytscan/yt-autoscanner"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/kw_stats.log"
SLEEP_SEC=30      # nghỉ giữa mỗi vòng

# -----------------------------
# PREPARE
# -----------------------------
mkdir -p "$LOG_DIR"

cd "$PROJECT_ROOT"

echo "----------------------------------------" | tee -a "$LOG_FILE"
echo "Starting auto_kw_stats.sh  (incremental)" | tee -a "$LOG_FILE"
echo "Using: $PYTHON_BIN -m worker.keyword_stats" | tee -a "$LOG_FILE"
echo "----------------------------------------" | tee -a "$LOG_FILE"

# -----------------------------
# MAIN LOOP
# -----------------------------
while true; do
    echo "[`date`] Running keyword_stats incremental..." | tee -a "$LOG_FILE"

    "$PYTHON_BIN" -m worker.keyword_stats \
        --mode incremental \
        --limit 500 \
        >> "$LOG_FILE" 2>&1

    echo "[`date`] Completed one run. Sleeping ${SLEEP_SEC}s..." | tee -a "$LOG_FILE"
    sleep $SLEEP_SEC
done
