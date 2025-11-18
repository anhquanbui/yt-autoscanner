#!/usr/bin/env bash
set -euo pipefail

# ==========================
# Paths
# ==========================
PROJECT_ROOT="/home/ytscan/yt-autoscanner"
VENV_PY="$PROJECT_ROOT/.venv/bin/python"

# ==========================
# Guard: venv must exist
# ==========================
if [ ! -x "$VENV_PY" ]; then
  echo "[FATAL] Python venv not found at: $VENV_PY"
  echo "-> Run:"
  echo "   python3 -m venv $PROJECT_ROOT/.venv"
  echo "   source $PROJECT_ROOT/.venv/bin/activate"
  echo "   pip install -r worker/requirements.txt"
  exit 1
fi

# NOTE:
# No manual .env loading here.
# All env resolution is handled inside Python by config.env.load_env(),
# which already checks:
#   1) ~/.env
#   2) <project_root>/.env
#   3) .env files in subfolders under project_root

# Always run from project root so Python imports work
cd "$PROJECT_ROOT"

# ==========================
# 6H low-quality loop
# ==========================
SLEEP_SECONDS=1800   # 30 minutes between runs

while true; do
  echo "[AutoLowQ-6H] $(date) running worker.low_quality_6h_worker"

  if ! "$VENV_PY" -m worker.low_quality_6h_worker; then
    rc=$?
    echo "[AutoLowQ-6H] worker exited with code $rc"

    # 88 = quota exhausted (EXIT_QUOTA)
    if [ "$rc" -eq 88 ]; then
      echo "[AutoLowQ-6H] quota exhausted, sleeping 600s"
      sleep 600
      continue
    fi
  fi

  echo "[AutoLowQ-6H] sleeping ${SLEEP_SECONDS}s"
  sleep "$SLEEP_SECONDS"
done
