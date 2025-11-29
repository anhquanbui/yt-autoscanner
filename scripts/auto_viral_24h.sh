#!/usr/bin/env bash
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
# Viral 24H model likely needs xgboost / sklearn / pymongo
# Uncomment if you want automatic install via requirements-dev.txt:
# ensure_module "xgboost"
# ensure_module "pymongo"

# ==========================
# Always run from project root
# ==========================
cd "$PROJECT_ROOT"

# ==========================
# Viral 24H loop
# ==========================
SLEEP_SECONDS=600   # 10 minutes (adjust if needed)

while true; do
  echo "[AutoViral-24H] $(date) running worker.viral_24h"

  if ! "$VENV_PY" -m worker.viral_24h; then
    rc=$?
    echo "[AutoViral-24H] worker exited with code $rc"

    # Add custom error handling here if needed
    sleep 600
    continue
  fi

  echo "[AutoViral-24H] sleeping ${SLEEP_SECONDS}s"
  sleep "$SLEEP_SECONDS"
done
