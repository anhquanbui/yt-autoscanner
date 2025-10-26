#!/usr/bin/env bash
# === MongoDB Auto Backup — BSON full + JSON (sampling) — keep last 4 ===
# Mode: Docker-optimized, stable (sequential), URI-based auth, empty-safe, two-step JSON compress
set -euo pipefail

# -------------------- Single-run lock --------------------
exec 9>/tmp/mongo_backup.lock
if ! flock -n 9; then
  echo "[Backup] Another run is already in progress. Exit."
  exit 0
fi

ts() { date '+%F %T'; }
start_ts="$(date +%s)"

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
echo "[Backup] $(ts) - Start"

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
    -e JSON_SKIP_COLLECTIONS="${JSON_SKIP_COLLECTIONS:-}" \
    -e JSON_SAMPLE_COLLECTIONS="${JSON_SAMPLE_COLLECTIONS:-videos}" \
    -e JSON_SAMPLE_LIMIT="${JSON_SAMPLE_LIMIT:-5000}" \
    -e JSON_MAX_DOCS="${JSON_MAX_DOCS:-0}" \
    "$MONGO_CONTAINER" bash -lc "
      set -euo pipefail

      rm -rf \"$IN_TMP\" \"$ARCHIVE_IN_CONTAINER\" || true
      mkdir -p \"$IN_TMP/json\"

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

      echo \"[Docker] List collections...\"
      mapfile -t COLLS < <(mongosh \"\$URI\" --quiet --eval \"db.getCollectionNames().forEach(c=>print(c))\")

      echo \"[Docker] Export JSON (sequential, empty-safe, sampling supported with two-step compress)...\"
      for coll in \"\${COLLS[@]}\"; do
        [ -n \"\$coll\" ] || continue
        out_gz=\"$IN_TMP/json/\$coll.json.gz\"
        tmp_json=\"$IN_TMP/json/\$coll.json.tmp\"
        log=\"$IN_TMP/json/\$coll.log\"

        # Skip by name (optional)
        if [ -n \"\${JSON_SKIP_COLLECTIONS:-}\" ] && printf '%s\n' \"\$JSON_SKIP_COLLECTIONS\" | tr ',' '\n' | grep -qx \"\$coll\"; then
          echo \"  - \$coll: skipped JSON (in skip list).\"
          continue
        fi

        # Count docs in DB_NAME explicitly
        doc_count=\$(mongosh \"\$URI\" --quiet \
          --eval \"db.getSiblingDB('$DB_NAME').getCollection('\$coll').estimatedDocumentCount()\" 2>>\"\$log\" || echo 0)
        case \"\$doc_count\" in ''|*[!0-9]*) doc_count=0 ;; esac

        # Auto-skip by MAX_DOCS (optional)
        if [ -n \"\${JSON_MAX_DOCS:-}\" ] && [ \"\${JSON_MAX_DOCS:-0}\" -gt 0 ] && [ \"\$doc_count\" -gt \"\${JSON_MAX_DOCS:-0}\" ]; then
          echo \"  - \$coll: \$doc_count docs > \${JSON_MAX_DOCS} → skip JSON (BSON backed).\"
          continue
        fi

        if [ \"\$doc_count\" -eq 0 ]; then
          echo \"  - \$coll: empty (0 docs)\"
          : | gzip -c > \"\$out_gz\"
          continue
        fi

        # Sampling
        LIMIT_ARGS=\"\"
        if [ -n \"\${JSON_SAMPLE_COLLECTIONS:-}\" ] && printf '%s\n' \"\$JSON_SAMPLE_COLLECTIONS\" | tr ',' '\n' | grep -qx \"\$coll\"; then
          LIMIT_ARGS=\"--limit \${JSON_SAMPLE_LIMIT:-5000}\"
          echo \"  - \$coll: exporting SAMPLE (\${JSON_SAMPLE_LIMIT:-5000} of ~\$doc_count)...\"
        else
          echo \"  - \$coll: exporting FULL (\$doc_count docs)...\"
        fi

        # Prefer pigz if available
        COMPRESS=\"gzip -c\"; command -v pigz >/dev/null 2>&1 && COMPRESS=\"pigz -c\"

        # Clean previous
        rm -f \"\$tmp_json\" \"\$out_gz\"

        # ---- Step 1: Export to plain JSON file (no pipe) ----
        if ! mongoexport --uri \"\$URI\" \
              --db \"$DB_NAME\" \
              --collection \"\$coll\" \
              --type=json \
              \${LIMIT_ARGS} \
              --out \"\$tmp_json\" >>\"\$log\" 2>&1; then
          echo \"❌ mongoexport failed: \$coll (log: \$log)\" >&2
          exit 1
        fi

        if [ ! -s \"\$tmp_json\" ]; then
          echo \"❌ mongoexport produced empty file for \$coll (log: \$log)\" >&2
          exit 1
        fi

        # ---- Step 2: Compress to .gz ----
        if ! eval \"\$COMPRESS\" < \"\$tmp_json\" > \"\$out_gz\"; then
          echo \"❌ compression failed for \$coll\" >&2
          exit 1
        fi
        rm -f \"\$tmp_json\"

        sz=\$(wc -c < \"\$out_gz\" || echo 0)
        if [ \"\$sz\" -lt 50 ]; then
          echo \"❌ Suspiciously small gzip for \$coll (size=\$sz B). See \$log\" >&2
          exit 1
        fi

        echo \"  - \$coll: export OK → \$(numfmt --to=iec \"\$sz\" 2>/dev/null || echo \${sz}B)\"
      done

      echo \"[Docker] Packing...\"
      tar -C \"$IN_TMP\" -czf \"$ARCHIVE_IN_CONTAINER\" .
      rm -rf \"$IN_TMP\"
    "

  docker cp "$MONGO_CONTAINER:$ARCHIVE_IN_CONTAINER" "$LOCAL_ARCHIVE"
  echo "[Backup] ✅ Copied to host: $LOCAL_ARCHIVE"

else
  echo "❌ Local mode not configured — please use Docker mode (set MONGO_CONTAINER)."
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
