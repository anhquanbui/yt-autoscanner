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
# Uncomment if this worker depends on extra libs from requirements-dev.txt
# ensure_module "pymongo"
# ensure_module "xgboost"

# NOTE:
# No manual .env loading here.
# All env resolution is handled inside Python by config.env.load_env().

# ==========================
# Always run from project root
# ==========================
cd "$PROJECT_ROOT"

# ==========================
# Low-quality auto loop
# ==========================
SLEEP_SECONDS=1800   # 30 minutes between runs

while true; do
  echo "[AutoQuality] $(date) starting worker.low_quality_autoflag"

  if ! "$VENV_PY" -m worker.low_quality_autoflag --only-missing; then
    rc=$?
    echo "[AutoQuality] low_quality_autoflag exited with code $rc"

    # 88 = quota exhausted
    if [ "$rc" -eq 88 ]; then
      echo "[AutoQuality] quota exhausted, sleeping 600s"
      sleep 600
      continue
    fi
  fi

  echo "[AutoQuality] sleeping ${SLEEP_SECONDS}s"
  sleep "$SLEEP_SECONDS"
done
