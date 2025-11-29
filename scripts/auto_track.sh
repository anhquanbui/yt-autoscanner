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
# For tracking you likely need pymongo, google-api-client, etc.
# Uncomment if you want automatic install via requirements-dev.txt:
# ensure_module "pymongo"

# NOTE:
# No manual .env loading here.
# Env is resolved in Python via config.env.load_env().

# ==========================
# Always run from project root
# ==========================
cd "$PROJECT_ROOT"

# ==========================
# Track loop
# ==========================
SLEEP_SECONDS=30   # time between runs (adjust as needed)

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
