#!/bin/bash
# === MongoDB Auto Backup (BSON + JSON; keep last 4) — using .env ===
set -euo pipefail

# Load secrets/config
ENV_FILE="/home/ytscan/yt-autoscanner/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "[Backup] ❌ .env not found at $ENV_FILE"
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

# Basic sanity checks
: "${MONGO_USER:?}"
: "${MONGO_PASS:?}"
: "${DB_NAME:?}"
: "${MONGO_CONTAINER:?}"
: "${BACKUP_DIR:?}"

DATE=$(date +%Y%m%d-%H%M%S)
REMOTE_NAME="${RCLONE_REMOTE_NAME:-}"
REMOTE_DIR="${RCLONE_REMOTE_DIR:-}"

mkdir -p "$BACKUP_DIR"
echo "[Backup] $(date) - Start (DB='$DB_NAME', container='$MONGO_CONTAINER')"

# 1) BSON + JSON export inside container
docker exec \
  -e DB_NAME="$DB_NAME" \
  -e MONGO_USER="$MONGO_USER" \
  -e MONGO_PASS="$MONGO_PASS" \
  "$MONGO_CONTAINER" bash -lc '
  set -euo pipefail
  ROOT="/data/db/backup-'"$DATE"'"
  mkdir -p "$ROOT/bson" "$ROOT/json"

  echo "[In-Container] Dump BSON..."
  mongodump \
    -u "$MONGO_USER" -p "$MONGO_PASS" \
    --authenticationDatabase admin \
    --db "$DB_NAME" \
    --out "$ROOT/bson"

  echo "[In-Container] Export JSON per-collection..."
  mapfile -t COLLS < <(mongosh --quiet \
    -u "$MONGO_USER" -p "$MONGO_PASS" --authenticationDatabase admin \
    --eval "db.getSiblingDB(\"$DB_NAME\").getCollectionNames().forEach(c=>print(c))")

  for coll in "${COLLS[@]}"; do
    [ -n "$coll" ] || continue
    echo "  - json: $coll"
    mongoexport \
      -u "$MONGO_USER" -p "$MONGO_PASS" \
      --authenticationDatabase admin \
      --db "$DB_NAME" \
      --collection "$coll" \
      --out "$ROOT/json/$coll.json"
  done
  echo "[In-Container] ✅ Done at $ROOT"
'

# 2) Copy to host
docker cp "$MONGO_CONTAINER:/data/db/backup-$DATE" "$BACKUP_DIR/"

# 3) Compress entire folder (bson + json)
tar -czf "$BACKUP_DIR/mongo-backup-$DATE.tar.gz" -C "$BACKUP_DIR" "backup-$DATE"

# 4) Remove raw export folder
rm -rf "$BACKUP_DIR/backup-$DATE"
echo "[Backup] ✅ Local archive: $BACKUP_DIR/mongo-backup-$DATE.tar.gz"

# 5) Keep only latest 4 backups locally
ls -1t "$BACKUP_DIR"/mongo-backup-*.tar.gz 2>/dev/null | sed -n '5,$p' | xargs -r rm -f
echo "[Backup] 🧹 Local retention enforced."

# 6) Upload to Google Drive if available
if command -v rclone >/dev/null 2>&1 && [ -n "${REMOTE_NAME}" ] && [ -n "${REMOTE_DIR}" ]; then
  REMOTE_PATH="${REMOTE_NAME}:${REMOTE_DIR}"
  rclone mkdir "$REMOTE_PATH" >/dev/null 2>&1 || true
  rclone copy "$BACKUP_DIR/mongo-backup-$DATE.tar.gz" "$REMOTE_PATH" --quiet
  echo "[Backup] ☁️ Uploaded."

  # 7) Remote retention
  rclone lsf "$REMOTE_PATH" --files-only \
    | grep -E "^mongo-backup-.*\.tar\.gz$" \
    | sort -r | sed -n '5,$p' \
    | while read -r old; do
        [ -n "$old" ] && rclone deletefile "$REMOTE_PATH/$old" --quiet
      done
  echo "[Backup] 🧹 Remote retention enforced."
else
  echo "[Backup] ⚠️ rclone not available, skipping cloud upload."
fi

echo "[Backup] ✅ Done."
