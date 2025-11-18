#!/usr/bin/env bash
set -euo pipefail

# === Paths ===
PROJECT_ROOT="/home/ytscan/yt-autoscanner"
VENV_PY="$PROJECT_ROOT/.venv/bin/python"
ENV_FILE="/home/ytscan/.env"

# === Guard: is venv exit?  ===
if [ ! -x "$VENV_PY" ]; then
  echo "[FATAL] Python venv not found at: $VENV_PY"
  echo "-> Run: python3 -m venv $PROJECT_ROOT/.venv && source $PROJECT_ROOT/.venv/bin/activate && pip install -r worker/requirements.txt"
  exit 1
fi

# === Load ~/.env if available, for global env ===
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

# 🚀 Always run from project root
cd "$PROJECT_ROOT"

# === Low-quality auto loop ===
SLEEP_SECONDS=1800

while true; do
  echo "[AutoQuality] $(date) starting worker.low_quality_autoflag"

  # --only-missing
  if ! "$VENV_PY" -m worker.low_quality_autoflag --only-missing; then
    rc=$?
    echo "[AutoQuality] low_quality_autoflag exited with code $rc"

    # Code 88
    if [ "$rc" -eq 88 ]; then
      echo "[AutoQuality] quota exhausted, sleeping 600s"
      sleep 600
      continue
    fi
  fi

  echo "[AutoQuality] sleeping ${SLEEP_SECONDS}s"
  sleep "$SLEEP_SECONDS"
done
