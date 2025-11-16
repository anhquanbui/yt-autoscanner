#!/usr/bin/env bash
set -euo pipefail

# === Paths ===
PROJECT_ROOT="/home/ytscan/yt-autoscanner"
VENV_PY="$PROJECT_ROOT/.venv/bin/python"
ENV_FILE="/home/ytscan/.env"

# === Guards ===
if [ ! -x "$VENV_PY" ]; then
  echo "[FATAL] Python venv not found at: $VENV_PY"
  echo "-> Run:  python3 -m venv $PROJECT_ROOT/.venv && source $PROJECT_ROOT/.venv/bin/activate && pip install -r worker/requirements.txt"
  exit 1
fi

# === Load .env (optional, cho system-wide env) ===
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

# === Luôn chạy từ project root ===
cd "$PROJECT_ROOT"

# === tracking loop ===
while true; do
  echo "[AutoTrack] $(date) starting worker.track_once"

  # luôn chạy bằng interpreter trong .venv, dưới dạng module
  if ! "$VENV_PY" -m worker.track_once; then
    rc=$?
    echo "[AutoTrack] track_once exited with code $rc"

    # tuỳ chọn: nếu code 88 là hết quota thì nghỉ lâu hơn
    if [ "$rc" -eq 88 ]; then
      echo "[AutoTrack] quota exhausted, sleeping 600s"
      sleep 600
      continue
    fi
  fi

  echo "[AutoTrack] sleeping 15s"
  sleep 15
done
