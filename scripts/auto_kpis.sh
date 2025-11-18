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
# config.env.load_env() inside Python will automatically find:
#   1) ~/.env
#   2) <project_root>/.env
#   3) .env files in any subfolder (recursive)

# Always run from project root to ensure correct imports
cd "$PROJECT_ROOT"

# ==========================
# KPI loop
# ==========================
SLEEP_SECONDS=300   # 5 minutes (change if needed)

while true; do
  echo "[AutoKPIs] $(date) running worker.compute_dashboard_kpis"

  if ! "$VENV_PY" -m worker.compute_dashboard_kpis; then
    rc=$?
    echo "[AutoKPIs] worker exited with code $rc"

    # 88 = quota exhausted (if KPIs later use API)
    if [ "$rc" -eq 88 ]; then
      echo "[AutoKPIs] quota exhausted, sleeping 600s"
      sleep 600
      continue
    fi
  fi

  echo "[AutoKPIs] sleeping ${SLEEP_SECONDS}s"
  sleep "$SLEEP_SECONDS"
done
