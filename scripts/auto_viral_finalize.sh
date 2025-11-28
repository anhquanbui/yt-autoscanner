#!/usr/bin/env bash
# auto_viral_finalize.sh — chạy vòng lặp viral_finalize

set -uo pipefail

REPO_DIR="/home/ytscan/yt-autoscanner/scripts"
VENV_DIR="$REPO_DIR/.venv"

# Interval giữa mỗi vòng finalize (giây).
# Có thể override bằng biến môi trường AUTO_VIRAL_FINALIZE_INTERVAL_SECONDS
FINALIZE_INTERVAL_SECONDS="${AUTO_VIRAL_FINALIZE_INTERVAL_SECONDS:-1800}"  # mặc định 30 phút

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
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "Starting auto_viral_finalize loop (interval = ${FINALIZE_INTERVAL_SECONDS}s)..."

while true; do
  log "=== New auto_viral_finalize cycle ==="

  # FINALIZE (gắn final.status = viral / non_viral / non_viral_lowq)
  log "[FINAL] Running: python -m worker.viral_finalize --only-missing --min-age-hours 24"
  if ! python -m worker.viral_finalize --only-missing --min-age-hours 24; then
    log "[ERROR] viral_finalize failed."
  fi

  log "Finalize cycle done. Sleeping ${FINALIZE_INTERVAL_SECONDS}s..."
  sleep "${FINALIZE_INTERVAL_SECONDS}"
done
