#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$PROJECT_DIR/backend/data"
MAINTENANCE_MARKER="$DATA_DIR/deployment-maintenance"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
GIT_SHA="$(git -C "$PROJECT_DIR" rev-parse --short=12 HEAD)"
IMMUTABLE_IMAGE="quantx-backend:active-drive-v2-$GIT_SHA"
STABLE_IMAGE="quantx-backend:active-drive-v2"
TEST_IMAGE="$STABLE_IMAGE"
if ! docker image inspect "$TEST_IMAGE" >/dev/null 2>&1; then
  TEST_IMAGE="quantx-ai-terminal-backend:v2"
fi
BACKUP_DIR="$PROJECT_DIR/backups/deploy-$TIMESTAMP"
VALIDATION_NAME="quantx-v2-validate-$TIMESTAMP"
VALIDATION_DATA="$BACKUP_DIR/validation-data"

failed() {
  touch "$MAINTENANCE_MARKER"
  chmod 600 "$MAINTENANCE_MARKER"
  printf '\nDEPLOYMENT FAILED CLOSED. Trading remains paused.\n'
  printf 'Existing production container was preserved unless the final switch had begun.\n'
  printf 'Inspect logs, then rerun this script. Do not remove the maintenance marker manually.\n'
}
trap failed ERR

mkdir -p "$BACKUP_DIR" "$VALIDATION_DATA"
touch "$MAINTENANCE_MARKER"
chmod 600 "$MAINTENANCE_MARKER"

# Defense in depth for an old image that predates the maintenance marker:
# revoke its live authority and activate the existing persistent kill switch.
docker exec quantx-backend python -c 'from app.core.config import settings; from app.trading import modes; settings.binance_live_enabled=False; modes.set_kill_switch(True, "deployment maintenance")'

docker exec quantx-backend python -c 'from app.trading.execution_router import router; r=router._blocked("open_position"); assert r and not r.ok'

cp "$DATA_DIR/paper.db" "$BACKUP_DIR/paper.db"
chmod 600 "$BACKUP_DIR/paper.db"
docker run --rm -v "$BACKUP_DIR:/backup:ro" python:3.12-slim python -c 'import sqlite3; db=sqlite3.connect("/backup/paper.db"); result=db.execute("PRAGMA integrity_check").fetchone()[0]; assert result == "ok", result'
cp "$BACKUP_DIR/paper.db" "$VALIDATION_DATA/paper.db"

docker run --rm -v "$PROJECT_DIR/backend:/app" -w /app -e SECRET_KEY=deployment-test-only "$TEST_IMAGE" python -m pytest tests -q

(cd "$PROJECT_DIR/frontend" && npm run test && npm run build)

docker build --build-arg "APP_GIT_SHA=$GIT_SHA" --build-arg "APP_IMAGE_TAG=$IMMUTABLE_IMAGE" -t "$IMMUTABLE_IMAGE" "$PROJECT_DIR/backend"
docker image inspect "$IMMUTABLE_IMAGE" >/dev/null

docker run -d --rm --name "$VALIDATION_NAME" -p 127.0.0.1:19000:8000 -v "$VALIDATION_DATA:/app/data" -e SECRET_KEY=isolated-validation-only -e PAPER_DATABASE_URL=sqlite:////app/data/paper.db -e DEPLOYMENT_MAINTENANCE_MODE=true -e REDIS_URL=redis://quantx-redis:6379/0 --network quantx-ai-terminal_default "$IMMUTABLE_IMAGE" >/dev/null

for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:19000/api/health >/dev/null && break
  sleep 1
done
curl -fsS http://127.0.0.1:19000/api/health | grep -q '"deployment_maintenance":true'
docker stop "$VALIDATION_NAME" >/dev/null

# Move the stable production pointer only after isolated validation succeeds.
docker tag "$IMMUTABLE_IMAGE" "$STABLE_IMAGE"
docker compose -f "$PROJECT_DIR/docker-compose.yml" up -d --no-deps --no-build --force-recreate backend

for _ in $(seq 1 60); do
  curl -fsS http://127.0.0.1:9000/api/health >/dev/null && break
  sleep 1
done
curl -fsS http://127.0.0.1:9000/api/health | grep -q '"deployment_maintenance":true'
curl -fsS https://www.quantxterminal.com/api/health >/dev/null
curl -fsSI https://www.quantxterminal.com >/dev/null
sudo nginx -t

RUNNING_IMAGE="$(docker inspect quantx-backend --format '{{.Image}}')"
EXPECTED_IMAGE="$(docker image inspect "$STABLE_IMAGE" --format '{{.Id}}')"
test "$RUNNING_IMAGE" = "$EXPECTED_IMAGE"
docker exec quantx-backend python -c 'from app.core.config import settings; from app.db.session import SessionLocal; from app.decision_engine.repository import get_setting; db=SessionLocal(); assert get_setting(db, settings.admin_username).decision_engine == "active_drive_v2"; db.close()'

printf '\nDeployment verified. Real execution remains disabled.\n'
printf 'Production URL: https://www.quantxterminal.com\n'
printf 'Image: %s (%s)\n' "$IMMUTABLE_IMAGE" "$EXPECTED_IMAGE"
printf 'Resume only with: %s/scripts/resume-live-trading.sh\n' "$PROJECT_DIR"
