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
# For viral model you probably need xgboost / pymongo / sklearn, etc.
# Uncomment if you want automatic install via requirements-dev.txt:
# ensure_module "xgboost"
# ensure_module "pymongo"

# ==========================
# Always run from project root
# ==========================
cd "$PROJECT_ROOT"

# ==========================
# Viral 6H loop
# ==========================
SLEEP_SECONDS=600   # 10 minutes between runs (adjust if needed)

while true; do
  echo "[AutoViral-6H] $(date) running worker.viral_6h"

  if ! "$VENV_PY" -m worker.viral_6h; then
    rc=$?
    echo "[AutoViral-6H] worker exited with code $rc"

    # You can add special handling for specific exit codes here
    sleep 600
    continue
  fi

  echo "[AutoViral-6H] sleeping ${SLEEP_SECONDS}s"
  sleep "$SLEEP_SECONDS"
done
