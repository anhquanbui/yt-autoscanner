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
# For low-quality model you likely need xgboost, sklearn, pymongo, etc.
# Uncomment as needed:
# ensure_module "xgboost"
# ensure_module "pymongo"

# ==========================
# Always run from project root so Python imports work
# ==========================
cd "$PROJECT_ROOT"

# ==========================
# 3H low-quality loop
# ==========================
SLEEP_SECONDS=1200   # 20 minutes between runs (adjust if needed)

while true; do
  echo "[AutoLowQ-3H] $(date) running worker.low_quality_3h_worker"

  if ! "$VENV_PY" -m worker.low_quality_3h_worker; then
    rc=$?
    echo "[AutoLowQ-3H] worker exited with code $rc"

    # 88 = quota exhausted (EXIT_QUOTA)
    if [ "$rc" -eq 88 ]; then
      echo "[AutoLowQ-3H] quota exhausted, sleeping 600s"
      sleep 600
      continue
    fi
  fi

  echo "[AutoLowQ-3H] sleeping ${SLEEP_SECONDS}s"
  sleep "$SLEEP_SECONDS"
done
