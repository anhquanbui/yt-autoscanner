#!/usr/bin/env bash
set -euo pipefail

# === Paths ===
PROJECT_ROOT="/home/ytscan/yt-autoscanner"
VENV_PY="$PROJECT_ROOT/.venv/bin/python"
ENV_FILE="/home/ytscan/.env"   # .env đặt ngoài repo cho an toàn

# === Guards ===
if [ ! -x "$VENV_PY" ]; then
  echo "[FATAL] Python venv not found at: $VENV_PY"
  echo "-> Run:  python3 -m venv $PROJECT_ROOT/.venv && source $PROJECT_ROOT/.venv/bin/activate && pip install -r worker/requirements.txt"
  exit 1
fi

# === Load .env (nếu có) ===
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

# === Vào thư mục worker ===
cd "$PROJECT_ROOT/worker"

# === Vòng lặp tracking ===
while true; do
  echo "[AutoTrack] $(date) starting track_once.py"

  # Luôn chạy bằng interpreter trong .venv
  if ! "$VENV_PY" track_once.py; then
    echo "[AutoTrack] track_once.py exited with non-zero code"
  fi

  echo "[AutoTrack] sleeping 15s"
  sleep 15
done
