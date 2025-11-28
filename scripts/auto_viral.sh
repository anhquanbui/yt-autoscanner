#!/usr/bin/env bash
# auto_viral.sh — chạy vòng lặp viral_prediction_core (6h / 12h / 24h, parallel)

set -uo pipefail

REPO_DIR="/home/ytscan/yt-autoscanner/scripts"
VENV_DIR="$REPO_DIR/.venv"

# Interval giữa mỗi vòng (giây). Có thể override bằng biến môi trường AUTO_VIRAL_INTERVAL_SECONDS
INTERVAL_SECONDS="${AUTO_VIRAL_INTERVAL_SECONDS:-1800}"  # mặc định 30 phút

cd "$REPO_DIR" || {
  echo "Cannot cd to $REPO_DIR"
  exit 1
}

# Activate venv nếu có
if [[ -d "$VENV_DIR" ]]; then
  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"
fi

log() {
  # In log có timestamp cho dễ debug
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "Starting auto_viral loop (interval = ${INTERVAL_SECONDS}s, parallel 6h/12h/24h)..."

while true; do
  log "=== New auto_viral cycle ==="

  # 6H STAGE (background)
  log "[6h] Running: python -m worker.viral_prediction_core 6h --only-missing"
  python -m worker.viral_prediction_core 6h --only-missing &
  PID_6H=$!

  # 12H STAGE (background)
  log "[12h] Running: python -m worker.viral_prediction_core 12h --only-missing"
  python -m worker.viral_prediction_core 12h --only-missing &
  PID_12H=$!

  # 24H STAGE (background)
  log "[24h] Running: python -m worker.viral_prediction_core 24h --only-missing"
  python -m worker.viral_prediction_core 24h --only-missing &
  PID_24H=$!

  # Đợi cả 3 stage xong
  log "Waiting for 6h/12h/24h jobs to finish..."
  wait "$PID_6H" || log "[ERROR] viral_prediction_core 6h failed."
  wait "$PID_12H" || log "[ERROR] viral_prediction_core 12h failed."
  wait "$PID_24H" || log "[ERROR] viral_prediction_core 24h failed."

  log "Cycle done. Sleeping ${INTERVAL_SECONDS}s..."
  sleep "${INTERVAL_SECONDS}"
done
