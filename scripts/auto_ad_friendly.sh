#!/usr/bin/env bash
# auto_ad_friendly.sh — run ad_friendly_worker in a safe infinite loop
# Interval: 30 seconds

set -euo pipefail

# Repo root
REPO_DIR="/home/ytscan/yt-autoscanner"
VENV_DIR="$REPO_DIR/.venv"

# Interval between runs (seconds)
INTERVAL_SECONDS=30

cd "$REPO_DIR" || {
  echo "Cannot cd to $REPO_DIR"
  exit 1
}

# Activate venv if exists
if [[ -d "$VENV_DIR" ]]; then
  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"
else
  echo "[WARN] venv not found, using system python"
fi

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "[auto_ad_friendly] Started loop (interval = ${INTERVAL_SECONDS}s)"

while true; do
  log "[auto_ad_friendly] Running: ad_friendly_worker --only-missing --no-collection-log"

  # Run worker — never let it crash the loop
  python -m worker.ad_friendly_worker --only-missing --no-collection-log || \
    log "[auto_ad_friendly] ERROR occurred in worker, continuing after sleep..."

  log "[auto_ad_friendly] Sleeping ${INTERVAL_SECONDS}s..."
  sleep "$INTERVAL_SECONDS"
done
