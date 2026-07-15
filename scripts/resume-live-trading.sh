#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="$PROJECT_DIR/backend/data/deployment-maintenance"
EXPECTED_IMAGE="$(docker image inspect quantx-backend:active-drive-v2 --format '{{.Id}}')"
RUNNING_IMAGE="$(docker inspect quantx-backend --format '{{.Image}}')"

test "$RUNNING_IMAGE" = "$EXPECTED_IMAGE"
curl -fsS http://127.0.0.1:9000/api/health >/dev/null
curl -fsS https://www.quantxterminal.com/api/health >/dev/null

docker exec quantx-backend python -c '
from app.core.config import settings
from app.db.session import SessionLocal
from app.decision_engine.repository import get_setting
db=SessionLocal()
try:
    assert get_setting(db, settings.admin_username).decision_engine == "active_drive_v2"
finally:
    db.close()
'

printf 'This only removes deployment maintenance. It does not enable the server live lock or user unlock.\n'
printf 'Type RESUME ACTIVE DRIVE V2 to continue: '
read -r confirmation
test "$confirmation" = "RESUME ACTIVE DRIVE V2"

rm -f "$MARKER"
docker exec quantx-backend python -c 'from app.deployment import maintenance; maintenance.disable(); assert not maintenance.enabled()'

printf 'Deployment maintenance removed. Live trading remains locked until both existing server and user unlock controls are deliberately enabled.\n'
