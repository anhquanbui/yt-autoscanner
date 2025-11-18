#!/usr/bin/env bash
# === MongoDB Auto Backup — BSON ONLY (archive+gzip) — keep last 4 ===
# Mode: Docker-optimized, stable (sequential), URI-based auth
set -euo pipefail

# --- Ensure PATH for cron (minimal env) ---
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# --- Timestamp all output (stdout + stderr) ---
if command -v awk >/dev/null 2>&1; then
  exec > >(awk '{ cmd="date +\"[%F %T]\""; cmd | getline ts; close(cmd); print ts, $0; fflush(); }') 2>&1
fi

# -------------------- Single-run lock --------------------
exec 9>/tmp/mongo_backup.lock
if ! flock -n 9; then
  echo "[Backup] Another run is already in progress. Exit."
  exit 0
fi

ts() { date '+%F %T'; }
start_ts="$(date +%s)"

echo "===== BACKUP START $(ts) ====="
echo "[Backup] Host: $(hostname) | User: $(id -un)"
echo "[Backup] PWD: $(pwd)"

# -------------------- Load .env --------------------
load_env() {
  local tried=0 p
  for p in "$(dirname "$0")/../.env" "$HOME/.env" ".env"; do
    if [ -f "$p" ]; then
      set -a; . "$p"; set +a
      echo "✅ Loaded .env: $p"
      tried=1
    fi
  done
  if [ "$tried" -eq 0 ]; then echo "⚠️ No .env found. Using current environment."; fi
}
load_env

# -------------------- Required configs --------------------
: "${DB_NAME:?Missing DB_NAME in env}"
: "${BACKUP_DIR:?Missing BACKUP_DIR in env}"

DATE="$(date +%Y%m%d-%H%M%S)"
REMOTE_NAME="${RCLONE_REMOTE_NAME:-}"
REMOTE_DIR="${RCLONE_REMOTE_DIR:-}"
LOCAL_ARCHIVE="$BACKUP_DIR/mongo-backup-$DATE.tar.gz"

mkdir -p "$BACKUP_DIR"
echo "[Backup] $(ts) - Start | DB_NAME=$DB_NAME | BACKUP_DIR=$BACKUP_DIR"

docker_has_container() {
  command -v docker >/dev/null && docker ps -a --format '{{.Names}}' | grep -qx "$1"
}

# ====================================================================
#                         DOCKER MODE (stable)
# ====================================================================
if [ -n "${MONGO_CONTAINER:-}" ] && docker_has_container "$MONGO_CONTAINER"; then
  echo "[Backup] Mode: Docker ($MONGO_CONTAINER)"

  IN_TMP="/tmp/ytmongo-backup-$DATE"
  ARCHIVE_IN_CONTAINER="/tmp/mongo-backup-$DATE.tar.gz"

  docker exec \
    -e DB_NAME="$DB_NAME" \
    -e MONGO_USER="${MONGO_USER:-}" \
    -e MONGO_PASS="${MONGO_PASS:-}" \
    -e MONGO_URI="${MONGO_URI:-}" \
    "$MONGO_CONTAINER" bash -lc "
      set -euo pipefail

      rm -rf \"$IN_TMP\" \"$ARCHIVE_IN_CONTAINER\" || true
      mkdir -p \"$IN_TMP\"

      # Build URI (inside container)
      if [ -n \"\${MONGO_URI:-}\" ]; then
        URI=\"\$MONGO_URI\"
      else
        URI=\"mongodb://\${MONGO_USER}:\${MONGO_PASS}@127.0.0.1:27017/\${DB_NAME}?authSource=admin\"
      fi
      export URI

      echo \"[Docker] Dump BSON (archive+gzip)...\"
      mongodump --uri \"\$URI\" --db \"\$DB_NAME\" \
        --gzip --archive=\"$IN_TMP/bson.archive.gz\"

      # Pack ONLY the BSON archive
      echo \"[Docker] Packing BSON archive...\"
      tar -C \"$IN_TMP\" -czf \"$ARCHIVE_IN_CONTAINER\" bson.archive.gz
      rm -rf \"$IN_TMP\"
    "

  docker cp "$MONGO_CONTAINER:$ARCHIVE_IN_CONTAINER" "$LOCAL_ARCHIVE"
  echo "[Backup] ✅ Copied to host: $LOCAL_ARCHIVE"

else
  echo "❌ Local mode not configured — please use Docker mode (set MONGO_CONTAINER)."
  echo "===== BACKUP END (FAILED) $(ts) ====="
  exit 1
fi

# -------------------- Local retention (keep last 4) --------------------
ls -1t "$BACKUP_DIR"/mongo-backup-*.tar.gz 2>/dev/null | sed -n '5,$p' | xargs -r rm -f
echo "[Backup] 🧹 Local retention enforced"

# -------------------- Cloud upload (optional, rclone) --------------------
if command -v rclone >/dev/null && [ -n "$REMOTE_NAME" ] && [ -n "$REMOTE_DIR" ]; then
  REMOTE_PATH="$REMOTE_NAME:$REMOTE_DIR"
  rclone mkdir "$REMOTE_PATH" >/dev/null 2>&1 || true
  rclone copy "$LOCAL_ARCHIVE" "$REMOTE_PATH" --quiet
  echo "[Backup] ☁️ Uploaded to cloud"

  rclone lsf "$REMOTE_PATH" --files-only \
    | grep -E '^mongo-backup-.*\.tar\.gz$' \
    | sort -r | sed -n '5,$p' \
    | while read -r old; do
        rclone deletefile "$REMOTE_PATH/$old" --quiet || true
      done
  echo "[Backup] 🧹 Cloud retention enforced"
fi

echo "[Backup] ✅ Completed in $(( $(date +%s) - start_ts ))s → $LOCAL_ARCHIVE"
echo "===== BACKUP END $(ts) ====="
