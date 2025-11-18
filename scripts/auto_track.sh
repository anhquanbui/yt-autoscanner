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
# Env is resolved in Python via config.env.load_env().

# Always run from project root
cd "$PROJECT_ROOT"

# ==========================
# Track loop
# ==========================
SLEEP_SECONDS=15   # time between runs (tùy bạn chỉnh)

while true; do
  echo "[AutoTrack] $(date) running worker.track_once"

  if ! "$VENV_PY" -m worker.track_once; then
    rc=$?
    echo "[AutoTrack] track_once exited with code $rc"

    # 88 = quota exhausted (EXIT_QUOTA)
    if [ "$rc" -eq 88 ]; then
      echo "[AutoTrack] quota exhausted, sleeping 600s"
      sleep 600
      continue
    fi
  fi

  echo "[AutoTrack] sleeping ${SLEEP_SECONDS}s"
  sleep "$SLEEP_SECONDS"
done
