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
# Viral model likely needs xgboost, sklearn, pymongo
# Uncomment if you want auto-install via requirements-dev.txt:
# ensure_module "xgboost"
# ensure_module "pymongo"

# ==========================
# Always run from project root
# ==========================
cd "$PROJECT_ROOT"

# ==========================
# Viral 12H loop
# ==========================
SLEEP_SECONDS=600   # 10 minutes (adjust if needed)

while true; do
  echo "[AutoViral-12H] $(date) running worker.viral_12h"

  if ! "$VENV_PY" -m worker.viral_12h; then
    rc=$?
    echo "[AutoViral-12H] worker exited with code $rc"

    # Custom handling for specific exit codes if needed
    sleep 600
    continue
  fi

  echo "[AutoViral-12H] sleeping ${SLEEP_SECONDS}s"
  sleep "$SLEEP_SECONDS"
done
