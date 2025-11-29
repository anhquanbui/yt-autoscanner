#!/usr/bin/env bash
# auto_viral_finalize.sh — loop runner for worker.viral_finalize

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
# ensure_module "pymongo"

# ==========================
# Finalize interval (seconds)
# Can be overridden via AUTO_VIRAL_FINALIZE_INTERVAL_SECONDS
# ==========================
FINALIZE_INTERVAL_SECONDS="${AUTO_VIRAL_FINALIZE_INTERVAL_SECONDS:-1800}"  # default: 30 minutes

# ==========================
# Always run from project root
# ==========================
cd "$PROJECT_ROOT" || {
  echo "[FATAL] Cannot cd to $PROJECT_ROOT"
  exit 1
}

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "Starting auto_viral_finalize loop (interval = ${FINALIZE_INTERVAL_SECONDS}s)..."

while true; do
  log "=== New auto_viral_finalize cycle ==="

  log "[FINAL] Running: $VENV_PY -m worker.viral_finalize --only-missing --min-age-hours 24"
  if ! "$VENV_PY" -m worker.viral_finalize --only-missing --min-age-hours 24; then
    rc=$?
    log "[ERROR] viral_finalize exited with code $rc"
    # You can add special handling for specific exit codes here if needed
  fi

  log "Finalize cycle done. Sleeping ${FINALIZE_INTERVAL_SECONDS}s..."
  sleep "${FINALIZE_INTERVAL_SECONDS}"
done
