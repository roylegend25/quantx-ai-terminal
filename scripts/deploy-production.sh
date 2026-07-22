#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$PROJECT_DIR/backend/data"
MAINTENANCE_MARKER="$DATA_DIR/deployment-maintenance"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
GIT_SHA="$(git -C "$PROJECT_DIR" rev-parse --short=12 HEAD)"
# Feature-slug-$sha-$timestamp naming, matching the convention actually in
# use for recent deployments (see .env's current BACKEND_IMAGE and
# `docker images` history) - the older hardcoded "active-drive-v2"
# STABLE_IMAGE floating-tag scheme below this comment in past revisions of
# this script no longer matches how docker-compose.yml picks up the running
# image (via the BACKEND_IMAGE env var read from .env), so this deploy
# updates .env directly instead of re-tagging a floating pointer.
IMMUTABLE_IMAGE="quantx-backend:bot-settings-scoped-risk-$GIT_SHA-$TIMESTAMP"
CURRENT_IMAGE="$(grep -E '^BACKEND_IMAGE=' "$PROJECT_DIR/.env" | cut -d= -f2-)"
TEST_IMAGE="$CURRENT_IMAGE"
if [ -z "$TEST_IMAGE" ] || ! docker image inspect "$TEST_IMAGE" >/dev/null 2>&1; then
  TEST_IMAGE="quantx-ai-terminal-backend:v2"
fi
BACKUP_DIR="$PROJECT_DIR/backups/deploy-$TIMESTAMP"
VALIDATION_NAME="quantx-v2-validate-$TIMESTAMP"
VALIDATION_DATA="$BACKUP_DIR/validation-data"

failed() {
  sudo touch "$MAINTENANCE_MARKER"
  sudo chmod 600 "$MAINTENANCE_MARKER"
  if [ -f "$BACKUP_DIR/.env.pre-deploy" ] && ! cmp -s "$BACKUP_DIR/.env.pre-deploy" "$PROJECT_DIR/.env"; then
    cp "$BACKUP_DIR/.env.pre-deploy" "$PROJECT_DIR/.env"
    printf 'Restored .env (BACKEND_IMAGE rollback pointer) to its pre-deploy value.\n'
  fi
  printf '\nDEPLOYMENT FAILED CLOSED. Trading remains paused.\n'
  printf 'Existing production container was preserved unless the final switch had begun.\n'
  printf 'Inspect logs, then rerun this script. Do not remove the maintenance marker manually.\n'
}
trap failed ERR

mkdir -p "$BACKUP_DIR" "$VALIDATION_DATA"
sudo touch "$MAINTENANCE_MARKER"
sudo chmod 600 "$MAINTENANCE_MARKER"

# Defense in depth for an old image that predates the maintenance marker:
# revoke its live authority and activate the existing persistent kill switch.
docker exec quantx-backend python -c 'from app.core.config import settings; from app.trading import modes; settings.binance_live_enabled=False; modes.set_kill_switch(True, "deployment maintenance")'

docker exec quantx-backend python -c 'from app.trading.execution_router import router; r=router._blocked("open_position"); assert r and not r.ok'

cp "$DATA_DIR/paper.db" "$BACKUP_DIR/paper.db"
chmod 600 "$BACKUP_DIR/paper.db"
docker run --rm -v "$BACKUP_DIR:/backup:ro" python:3.12-slim python -c 'import sqlite3; db=sqlite3.connect("/backup/paper.db"); result=db.execute("PRAGMA integrity_check").fetchone()[0]; assert result == "ok", result'
cp "$BACKUP_DIR/paper.db" "$VALIDATION_DATA/paper.db"

# .env backup (holds BACKEND_IMAGE, the rollback pointer this script edits below).
cp "$PROJECT_DIR/.env" "$BACKUP_DIR/.env.pre-deploy"
chmod 600 "$BACKUP_DIR/.env.pre-deploy"

docker run --rm -v "$PROJECT_DIR/backend:/app" -w /app -e SECRET_KEY=deployment-test-only "$TEST_IMAGE" python -m pytest tests -q

# --- Bot Settings scoped-risk / indicator-performance test files (explicit,
# in addition to the full suite above, so a narrower rerun is fast if only
# these are suspect) ---
docker run --rm -v "$PROJECT_DIR/backend:/app" -w /app -e SECRET_KEY=deployment-test-only "$TEST_IMAGE" \
  python -m pytest tests/test_risk_settings_scope.py tests/test_active_drive_v2_point_margin.py \
  tests/test_indicator_performance.py tests/test_shadow_execution.py tests/test_star_recommendation.py \
  tests/test_indicator_notifications.py tests/test_indicator_eligibility_audit.py -q

(cd "$PROJECT_DIR/frontend" && npm run test && npm run build)

docker build --build-arg "APP_GIT_SHA=$GIT_SHA" --build-arg "APP_IMAGE_TAG=$IMMUTABLE_IMAGE" -t "$IMMUTABLE_IMAGE" "$PROJECT_DIR/backend"
docker image inspect "$IMMUTABLE_IMAGE" >/dev/null

docker run -d --rm --name "$VALIDATION_NAME" -p 127.0.0.1:19000:8000 -v "$VALIDATION_DATA:/app/data" -e SECRET_KEY=isolated-validation-only -e PAPER_DATABASE_URL=sqlite:////app/data/paper.db -e DEPLOYMENT_MAINTENANCE_MODE=true -e REDIS_URL=redis://quantx-redis:6379/0 --network quantx-ai-terminal_default "$IMMUTABLE_IMAGE" >/dev/null

# 180s, not 30s: init_db()'s migrations/schema checks run against the full
# production-scale database copy here (hundreds of thousands of rows), which
# takes meaningfully longer to become ready than a small/empty test database.
for _ in $(seq 1 180); do
  curl -fsS http://127.0.0.1:19000/api/health >/dev/null && break
  sleep 1
done
curl -fsS http://127.0.0.1:19000/api/health | grep -q '"deployment_maintenance":true'

# --- >=100 shadow evaluations against the isolated validation container's
# own paper.db copy (a copy of real production history, not an empty
# database - never production/backup data itself). replay_shadow_check.py
# asserts internally that the replay creates zero NEW Trade rows relative
# to whatever pre-existing trade history the copy already contains. ---
docker exec "$VALIDATION_NAME" python -m app.decision_engine.replay_shadow_check --min-evaluations 100

# --- Paper/Binance Real settings are genuinely separate rows in this image ---
docker exec "$VALIDATION_NAME" python -c '
from app.risk import settings_repository
paper = settings_repository.get_settings(scope="paper")
real = settings_repository.get_settings(scope="binance_real")
assert paper["scope"] == "paper" and real["scope"] == "binance_real"
assert "min_point_margin" in paper and "min_point_margin" in real
assert "min_total_evidence" in paper and "min_total_evidence" in real
'

docker stop "$VALIDATION_NAME" >/dev/null

# Point production at the new immutable image only after isolated validation
# succeeds - update .env's BACKEND_IMAGE (what docker-compose.yml actually
# reads) rather than a floating docker tag, matching current practice.
if grep -qE '^BACKEND_IMAGE=' "$PROJECT_DIR/.env"; then
  sed -i "s|^BACKEND_IMAGE=.*|BACKEND_IMAGE=$IMMUTABLE_IMAGE|" "$PROJECT_DIR/.env"
else
  printf 'BACKEND_IMAGE=%s\n' "$IMMUTABLE_IMAGE" >> "$PROJECT_DIR/.env"
fi
docker compose -f "$PROJECT_DIR/docker-compose.yml" --env-file "$PROJECT_DIR/.env" up -d --no-deps --no-build --force-recreate backend

for _ in $(seq 1 180); do
  curl -fsS http://127.0.0.1:9000/api/health >/dev/null && break
  sleep 1
done
curl -fsS http://127.0.0.1:9000/api/health | grep -q '"deployment_maintenance":true'
curl -fsS https://www.quantxterminal.com/api/health >/dev/null
curl -fsSI https://www.quantxterminal.com >/dev/null
sudo nginx -t

RUNNING_IMAGE="$(docker inspect quantx-backend --format '{{.Image}}')"
EXPECTED_IMAGE="$(docker image inspect "$IMMUTABLE_IMAGE" --format '{{.Id}}')"
test "$RUNNING_IMAGE" = "$EXPECTED_IMAGE"
docker exec quantx-backend python -c 'from app.core.config import settings; from app.db.session import SessionLocal; from app.decision_engine.repository import get_setting; db=SessionLocal(); assert get_setting(db, settings.admin_username).decision_engine == "active_drive_v2"; db.close()'

# --- Real Binance positions/orders are still zero after the swap, and no
# live-trading permission changed (read-only signed GET; never places an
# order) ---
docker exec -w /app quantx-backend python -c '
import asyncio
from app.exchanges.binance_futures_client import BinanceFuturesClient

async def main():
    client = BinanceFuturesClient(testnet=False, read_only=True)
    positions = await client.get_positions()
    orders = await client.get_open_orders()
    assert len(positions) == 0, f"expected zero real positions post-deploy, found {positions}"
    assert len(orders) == 0, f"expected zero real open orders post-deploy, found {orders}"

asyncio.run(main())
'
docker exec quantx-backend python -c '
from app.core.config import settings
assert settings.binance_live_enabled is False, "BINANCE_LIVE_ENABLED must remain false after deploy"
'

printf '\nDeployment verified. Real execution remains disabled.\n'
printf 'Production URL: https://www.quantxterminal.com\n'
printf 'Image: %s (%s)\n' "$IMMUTABLE_IMAGE" "$EXPECTED_IMAGE"
printf 'Resume only with: %s/scripts/resume-live-trading.sh\n' "$PROJECT_DIR"
