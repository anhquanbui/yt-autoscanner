#!/usr/bin/env bash
# === MongoDB Auto Backup (BSON + JSON; keep last 4) — using .env ===
set -euo pipefail

# --- Load .env (project root -> $HOME -> CWD) ---
load_env() {
  local tried=0
  local p

  p="$(dirname "$0")/../.env"
  if [ -f "$p" ]; then
    set -a; . "$p"; set +a
    echo "✅ Loaded .env: $p"; tried=1
  fi

  p="$HOME/.env"
  if [ -f "$p" ]; then
    set -a; . "$p"; set +a
    echo "✅ Loaded .env: $p"; tried=1
  fi

  p=".env"
  if [ -f "$p" ]; then
    set -a; . "$p"; set +a
    echo "✅ Loaded .env: $p"; tried=1
  fi

  if [ "$tried" -eq 0 ]; then
    echo "⚠️ No .env found. Using current environment."
  fi
}
load_env

# --- Required configs (pick either Docker mode or Local mode) ---
# Docker mode requires: MONGO_CONTAINER, DB_NAME, MONGO_USER, MONGO_PASS, BACKUP_DIR
# Local  mode prefers:  MONGO_URI and BACKUP_DIR (or DB_NAME + auth if needed)

: "${DB_NAME:?Missing DB_NAME in env}"
: "${BACKUP_DIR:?Missing BACKUP_DIR in env}"

DATE="$(date +%Y%m%d-%H%M%S)"
REMOTE_NAME="${RCLONE_REMOTE_NAME:-}"
REMOTE_DIR="${RCLONE_REMOTE_DIR:-}"

mkdir -p "$BACKUP_DIR"

echo "[Backup] $(date) - Start"

docker_has_container() {
  local name="$1"
  command -v docker >/dev/null 2>&1 || return 1
  docker ps -a --format '{{.Names}}' | grep -qx "$name"
}

# -------------------- DOCKER MODE --------------------
if [ -n "${MONGO_CONTAINER:-}" ] && docker_has_container "$MONGO_CONTAINER"; then
  : "${MONGO_USER:?Missing MONGO_USER for Docker mode}"
  : "${MONGO_PASS:?Missing MONGO_PASS for Docker mode}"

  echo "[Backup] Mode: Docker container='$MONGO_CONTAINER', DB='$DB_NAME'"

  # 1) In-container dump (BSON + per-collection JSON)
  docker exec \
  -e DB_NAME="$DB_NAME" \
  -e MONGO_USER="$MONGO_USER" \
  -e MONGO_PASS="$MONGO_PASS" \
  "$MONGO_CONTAINER" bash -lc "
  set -euo pipefail
  ROOT=\"/data/db/backup-$DATE\"
  mkdir -p \"\$ROOT/bson\" \"\$ROOT/json\"

  echo \"[In-Container] Dump BSON...\"
  mongodump \
    -u \"\$MONGO_USER\" -p \"\$MONGO_PASS\" \
    --authenticationDatabase admin \
    --db \"\$DB_NAME\" \
    --out \"\$ROOT/bson\"

  echo \"[In-Container] Export JSON per-collection...\"
  mapfile -t COLLS < <(mongosh --quiet \
    -u \"\$MONGO_USER\" -p \"\$MONGO_PASS\" --authenticationDatabase admin \
    --eval \"db.getSiblingDB(\\\"\$DB_NAME\\\").getCollectionNames().forEach(c=>print(c))\")

  for coll in \"\${COLLS[@]}\"; do
    [ -n \"\$coll\" ] || continue
    echo \"  - json: \$coll\"
    mongoexport \
      -u \"\$MONGO_USER\" -p \"\$MONGO_PASS\" \
      --authenticationDatabase admin \
      --db \"\$DB_NAME\" \
      --collection \"\$coll\" \
      --out \"\$ROOT/json/\$coll.json\"
  done
  echo \"[In-Container] ✅ Done at \$ROOT\"
"

  # 2) Copy to host
  docker cp "$MONGO_CONTAINER:/data/db/backup-$DATE" "$BACKUP_DIR/"

# -------------------- LOCAL MODE --------------------
else
  echo "[Backup] Mode: Local mongodump"

  # Ưu tiên MONGO_URI; nếu không có thì dùng auth rời rạc (nếu bạn đặt)
  if command -v mongosh >/dev/null 2>&1; then :; else
    echo "❌ mongosh not found (apt install -y mongodb-clients)."; exit 1
  fi
  if command -v mongodump >/dev/null 2>&1; then :; else
    echo "❌ mongodump not found (apt install -y mongodb-database-tools)."; exit 1
  fi

  ROOT="$BACKUP_DIR/backup-$DATE"
  mkdir -p "$ROOT/bson" "$ROOT/json"

  echo "[Local] Dump BSON..."
  if [ -n "${MONGO_URI:-}" ]; then
    mongodump --uri "$MONGO_URI" --db "$DB_NAME" --out "$ROOT/bson"
  else
    # Nếu không dùng URI, bạn có thể thêm các biến: MONGO_HOST, MONGO_PORT, MONGO_USER, MONGO_PASS
    : "${MONGO_HOST:=localhost}"
    : "${MONGO_PORT:=27017}"
    if [ -n "${MONGO_USER:-}" ] && [ -n "${MONGO_PASS:-}" ]; then
      mongodump -h "$MONGO_HOST:$MONGO_PORT" -u "$MONGO_USER" -p "$MONGO_PASS" \
        --authenticationDatabase admin --db "$DB_NAME" --out "$ROOT/bson"
    else
      mongodump -h "$MONGO_HOST:$MONGO_PORT" --db "$DB_NAME" --out "$ROOT/bson"
    fi
  fi

  echo "[Local] Export JSON per-collection..."
  if [ -n "${MONGO_URI:-}" ]; then
    mapfile -t COLLS < <(mongosh "$MONGO_URI" --quiet --eval "db.getSiblingDB('$DB_NAME').getCollectionNames().forEach(c=>print(c))")
  else
    if [ -n "${MONGO_USER:-}" ] && [ -n "${MONGO_PASS:-}" ]; then
      mapfile -t COLLS < <(mongosh -u "$MONGO_USER" -p "$MONGO_PASS" --authenticationDatabase admin --quiet \
        --eval "db.getSiblingDB('$DB_NAME').getCollectionNames().forEach(c=>print(c))")
    else
      mapfile -t COLLS < <(mongosh --quiet --eval "db.getSiblingDB('$DB_NAME').getCollectionNames().forEach(c=>print(c))")
    fi
  fi

  for coll in "${COLLS[@]}"; do
    [ -n "$coll" ] || continue
    echo "  - json: $coll"
    if [ -n "${MONGO_URI:-}" ]; then
      mongoexport --uri "$MONGO_URI" --db "$DB_NAME" --collection "$coll" --out "$ROOT/json/$coll.json"
    else
      if [ -n "${MONGO_USER:-}" ] && [ -n "${MONGO_PASS:-}" ]; then
        mongoexport -u "$MONGO_USER" -p "$MONGO_PASS" --authenticationDatabase admin \
          --db "$DB_NAME" --collection "$coll" --out "$ROOT/json/$coll.json"
      else
        mongoexport --db "$DB_NAME" --collection "$coll" --out "$ROOT/json/$coll.json"
      fi
    fi
  done
  echo "[Local] ✅ Done at $ROOT"
fi

# 3) Compress entire folder (bson + json)
tar -czf "$BACKUP_DIR/mongo-backup-$DATE.tar.gz" -C "$BACKUP_DIR" "backup-$DATE"

# 4) Remove raw export folder
rm -rf "$BACKUP_DIR/backup-$DATE"
echo "[Backup] ✅ Local archive: $BACKUP_DIR/mongo-backup-$DATE.tar.gz"

# 5) Keep only latest 4 backups locally
ls -1t "$BACKUP_DIR"/mongo-backup-*.tar.gz 2>/dev/null | sed -n '5,$p' | xargs -r rm -f
echo "[Backup] 🧹 Local retention enforced."

# 6) Upload to Google Drive if available
if command -v rclone >/dev/null 2>&1 && [ -n "$REMOTE_NAME" ] && [ -n "$REMOTE_DIR" ]; then
  REMOTE_PATH="${REMOTE_NAME}:${REMOTE_DIR}"
  rclone mkdir "$REMOTE_PATH" >/dev/null 2>&1 || true
  rclone copy "$BACKUP_DIR/mongo-backup-$DATE.tar.gz" "$REMOTE_PATH" --quiet
  echo "[Backup] ☁️ Uploaded."

  # 7) Remote retention (keep last 4)
  rclone lsf "$REMOTE_PATH" --files-only \
    | grep -E '^mongo-backup-.*\.tar\.gz$' \
    | sort -r | sed -n '5,$p' \
    | while read -r old; do
        [ -n "$old" ] && rclone deletefile "$REMOTE_PATH/$old" --quiet || true
      done
  echo "[Backup] 🧹 Remote retention enforced."
else
  echo "[Backup] ⚠️ rclone not available, skipping cloud upload."
fi

echo "[Backup] ✅ Done."
