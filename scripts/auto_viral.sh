#!/usr/bin/env bash
# auto_viral.sh — loop runner for viral_prediction_core (6h / 12h / 24h in parallel)

set -euo pipefail

# ==========================
# Shared environment
# ==========================
source "$(dirname "$0")/../config/env.sh"

# ==========================
# Ensure venv exists
# ==========================
check_venv

# Optionally ensure required modules exist
# Uncomment if you want to enforce dependencies via requirements-dev.txt:
# ensure_module "xgboost"
# ensure_module "pymongo"

# ==========================
# Interval between cycles (seconds)
# Can be overridden via AUTO_VIRAL_INTERVAL_SECONDS
# ==========================
INTERVAL_SECONDS="${AUTO_VIRAL_INTERVAL_SECONDS:-1800}"  # default 30 minutes

# ==========================
# Always run from project root
# ==========================
cd "$PROJECT_ROOT" || {
  echo "[FATAL] Cannot cd to $PROJECT_ROOT"
  exit 1
}

log() {
  # Simple timestamped logger
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "Starting auto_viral loop (interval = ${INTERVAL_SECONDS}s, parallel 6h/12h/24h)..."
log "Using python: $VENV_PY"
log "Current dir: $(pwd)"

while true; do
  log "=== New auto_viral cycle ==="

  # 6H STAGE (background)
  log "[6h] Running: $VENV_PY -m worker.viral_prediction_core 6h --only-missing"
  "$VENV_PY" -m worker.viral_prediction_core 6h --only-missing &
  PID_6H=$!

  # 12H STAGE (background)
  log "[12h] Running: $VENV_PY -m worker.viral_prediction_core 12h --only-missing"
  "$VENV_PY" -m worker.viral_prediction_core 12h --only-missing &
  PID_12H=$!

  # 24H STAGE (background)
  log "[24h] Running: $VENV_PY -m worker.viral_prediction_core 24h --only-missing"
  "$VENV_PY" -m worker.viral_prediction_core 24h --only-missing &
  PID_24H=$!

  # Wait for all three stages to finish
  log "Waiting for 6h/12h/24h jobs to finish..."
  wait "$PID_6H" || log "[ERROR] viral_prediction_core 6h failed."
  wait "$PID_12H" || log "[ERROR] viral_prediction_core 12h failed."
  wait "$PID_24H" || log "[ERROR] viral_prediction_core 24h failed."

  log "Cycle done. Sleeping ${INTERVAL_SECONDS}s..."
  sleep "${INTERVAL_SECONDS}"
done
