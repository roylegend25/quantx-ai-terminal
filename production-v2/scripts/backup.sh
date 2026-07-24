#!/usr/bin/env bash
# QuantX Production V2 — SQLite online backup.
# Uses SQLite's own `.backup` command (safe on a live WAL-mode database,
# unlike copying the file directly) via the running backend container, so
# taking a backup never needs to stop the app. Called by
# quantx-v2-backup.timer, or run manually before an update (see
# docs/UPDATE_GUIDE.md / docs/BACKUP_GUIDE.md).
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
set -a; source .env; set +a

BACKUP_DIR="${BACKUP_DIR:-/opt/quantx/backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST_DIR="$BACKUP_DIR/$TIMESTAMP"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

mkdir -p "$DEST_DIR"

echo "==> Backing up paper.db (SQLite online backup, safe under WAL)"
# The backend image has no `sqlite3` CLI (not in backend/Dockerfile's apt
# packages) — Python's stdlib sqlite3 module ships with every Python image
# and its Connection.backup() is the same safe, live-database backup API,
# so this needs no image change.
docker compose exec -T backend python3 -c "
import sqlite3
src = sqlite3.connect('/app/data/paper.db')
dst = sqlite3.connect('/app/data/backup-${TIMESTAMP}.db')
with dst:
    src.backup(dst)
src.close(); dst.close()
"
docker compose cp "backend:/app/data/backup-${TIMESTAMP}.db" "$DEST_DIR/paper.db"
docker compose exec -T backend rm -f "/app/data/backup-${TIMESTAMP}.db"

echo "==> Backing up .env (secrets — restrict access on the backup host too)"
cp .env "$DEST_DIR/.env"
chmod 600 "$DEST_DIR/.env"

echo "==> Pruning backups older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -exec rm -rf {} +

echo "==> Backup complete: $DEST_DIR"
