#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="$PROJECT_DIR/backend/data/deployment-maintenance"
EXPECTED_IMAGE="$(docker image inspect quantx-backend:active-drive-v2 --format '{{.Id}}')"
RUNNING_IMAGE="$(docker inspect quantx-backend --format '{{.Image}}')"

test "$RUNNING_IMAGE" = "$EXPECTED_IMAGE"
test "$(docker ps --filter name=^/quantx-backend$ --format '{{.ID}}' | wc -l)" -eq 1
curl -fsS http://127.0.0.1:9000/api/health >/dev/null
curl -fsS https://www.quantxterminal.com/api/health >/dev/null
docker exec quantx-redis redis-cli ping | grep -qx PONG

docker exec quantx-backend python -c '
import httpx
from sqlalchemy import inspect
from app.core.config import settings
from app.core.env_manager import apply_to_settings
from app.core.security import create_internal_service_token
from app.db.session import SessionLocal
from app.decision_engine.repository import get_setting
from app.trading import modes
db=SessionLocal()
try:
    apply_to_settings()
    assert get_setting(db, settings.admin_username).decision_engine == "active_drive_v2"
    required={"active_drive_decisions","prediction_ledger","signal_candidates","user_bot_settings"}
    assert required.issubset(set(inspect(db.bind).get_table_names()))
    control=modes.get_control(db)
    assert settings.binance_live_enabled, "Server live lock is not intentionally enabled"
    assert control["live_unlocked"], "User live unlock is not intentionally enabled"
finally:
    db.close()
s=httpx.get(
    "http://127.0.0.1:8000/api/binance/snapshot",
    headers={"Authorization":"Bearer "+create_internal_service_token()},
    timeout=30,
).json()
assert not s.get("stale"), "Binance account snapshot is stale"
assert not s.get("errors"), "Binance account snapshot contains errors"
positions=s.get("positions") or []
orders=s.get("orders") or []
assert not orders, "Unexpected Binance open orders exist"
assert not [p for p in positions if p.get("protection_status") not in (None, "PROTECTED")], "Unprotected Binance position exists"
'

printf 'This only removes deployment maintenance. It does not enable the server live lock or user unlock.\n'
printf 'Type RESUME ACTIVE DRIVE V2 to continue: '
read -r confirmation
test "$confirmation" = "RESUME ACTIVE DRIVE V2"

rm -f "$MARKER"
docker exec quantx-backend python -c 'from app.deployment import maintenance; maintenance.disable(); assert not maintenance.enabled()'

printf 'Deployment maintenance removed. Live trading remains locked until both existing server and user unlock controls are deliberately enabled.\n'
