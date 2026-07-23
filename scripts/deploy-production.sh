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
IMMUTABLE_IMAGE="quantx-backend:v2-only-perf-cleanup-$GIT_SHA-$TIMESTAMP"
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

# A plain `cp` of paper.db while quantx-backend is actively writing to it
# (scheduler/resolver cycles) is not atomic with respect to SQLite's own
# transaction boundaries - it can capture a page-level-valid but logically
# inconsistent snapshot (e.g. an index reflecting a slightly different
# point in time than its table), which still passes PRAGMA integrity_check
# (that only verifies b-tree structure) but can fail a real cross-table/
# index consistency check later. Use SQLite's own online backup API
# instead, which is consistent by construction even against a live,
# actively-written source database.
docker exec quantx-backend python -c '
import sqlite3
src = sqlite3.connect("file:/app/data/paper.db?mode=ro", uri=True)
dst = sqlite3.connect("/tmp/deploy_backup_paper.db")
src.backup(dst)
dst.close()
src.close()
'
docker cp quantx-backend:/tmp/deploy_backup_paper.db "$BACKUP_DIR/paper.db"
docker exec quantx-backend rm -f /tmp/deploy_backup_paper.db
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

# --- Decision/Execution Pipeline link test files (explicit, in addition to
# the full suite above) - the unified timeframe resolver, pure pipeline-
# state derivation, the new unified API, the Binance Real dry-run, and the
# 3 deterministic paper smoke scenarios ---
docker run --rm -v "$PROJECT_DIR/backend:/app" -w /app -e SECRET_KEY=deployment-test-only "$TEST_IMAGE" \
  python -m pytest tests/test_current_pipeline_state.py tests/test_pipeline_api.py \
  tests/test_binance_real_dry_run.py tests/test_paper_smoke_scenarios.py \
  tests/test_horizon_authority.py tests/test_execution_fencing.py tests/test_execution_transparency_api.py \
  tests/test_execution_router_binance.py tests/test_trading_horizon.py -q

# --- V2-only / performance-cleanup test files (explicit, in addition to
# the full suite above) - Active Drive V1 disabled in production, N+1
# performance-lookup batching, authority TTL/scheduler-cadence gap fix,
# concurrent dashboard fan-out ---
docker run --rm -v "$PROJECT_DIR/backend:/app" -w /app -e SECRET_KEY=deployment-test-only "$TEST_IMAGE" \
  python -m pytest tests/test_legacy_engine_gated.py tests/test_v2_performance_batching.py \
  tests/test_authority_ttl_scheduler_gap.py tests/test_dashboard_concurrent_fanout.py \
  tests/test_history_counts_cache_and_index.py \
  tests/test_active_drive_v2.py tests/test_forecast_points.py tests/test_prediction_multi_symbol.py -q

(cd "$PROJECT_DIR/frontend" && npm run test -- --run && npx tsc --noEmit && npm run build)

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

# --- Decision/Execution Pipeline provenance migration must be physically
# present on the validation copy of the real production database before
# anything else runs - a model change without this would make
# check_schema_compatibility() fail and issue_horizon_authority start
# raising TRADING_HORIZON_MIGRATION_REQUIRED on every cycle, silently
# stopping all paper trading. ---
docker exec "$VALIDATION_NAME" python -c '
from app.db.init_db import check_schema_compatibility
from app.db.session import engine
status = check_schema_compatibility(engine)
assert status["compatible"] is True, status
from sqlalchemy import inspect
inspector = inspect(engine)
for table, columns in (
    ("trades", {"cycle_id"}),
    ("binance_bot_trades", {"decision_id", "authority_id", "execution_mode", "edge_at_entry", "cycle_id"}),
    ("binance_execution_attempts", {"decision_id", "authority_id", "execution_mode", "edge_at_entry", "cycle_id"}),
):
    existing = {c["name"] for c in inspector.get_columns(table)}
    missing = columns - existing
    assert not missing, f"{table} missing {missing}"
'

# (The 3 deterministic paper smoke scenarios - edge blocked -> no execution
# request; fully valid -> exactly one paper request/order, never
# duplicated; Binance Real dry-run blocked before the live client - already
# ran above against $TEST_IMAGE with the current code via pytest, which is
# available there through the bind-mounted tests/ directory. The built
# runtime image below intentionally has neither pytest nor tests/ - see
# backend/Dockerfile - so isolated-validation-container checks here are
# limited to what the deployed image itself can execute: schema
# compatibility, column presence, and the shadow-replay volume check.)

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
from app.decision_engine.router import decision_engine_router
from app.decision_engine.types import DecisionEngineType
assert settings.binance_live_enabled is False, "BINANCE_LIVE_ENABLED must remain false after deploy"
assert DecisionEngineType.ACTIVE_DRIVE_V1 not in decision_engine_router.engines, "Active Drive V1 (legacy - now the standalone QuantX Classic repository) must have no registered engine in Premium X Dark"
assert settings.default_decision_engine == "active_drive_v2", "Active Drive V2 must remain the default/only production decision engine"
'

# --- The unified current-pipeline API is reachable and internally
# consistent for the default symbol (same decision/authority family across
# every field it reports - no contradictory "approved" + "no signal"
# combination is even representable by this response shape). ---
docker exec quantx-backend python -c '
from app.db.session import SessionLocal
from app.core.config import settings
from app.trading_horizon.current_authority import resolve_authoritative_timeframe
from app.trading_horizon.diagnostics import current_pipeline_snapshot, derive_pipeline_state
import asyncio

async def main():
    db = SessionLocal()
    try:
        resolution = await resolve_authoritative_timeframe(db, settings.admin_username, settings.default_symbol)
        timeframe = resolution["execution_timeframe"]
        snapshot = current_pipeline_snapshot(db, user_id=settings.admin_username, symbol=settings.default_symbol,
                                             timeframe=timeframe)
        state, reason = derive_pipeline_state(snapshot)
        source = resolution["source"]
        print("current pipeline for " + settings.default_symbol + ": timeframe=" + timeframe +
              " source=" + source + " state=" + state + " reason=" + str(reason))
    finally:
        db.close()

asyncio.run(main())
'

printf '\nDeployment verified. Real execution remains disabled.\n'
printf 'Production URL: https://www.quantxterminal.com\n'
printf 'Image: %s (%s)\n' "$IMMUTABLE_IMAGE" "$EXPECTED_IMAGE"
printf 'Resume only with: %s/scripts/resume-live-trading.sh\n' "$PROJECT_DIR"
