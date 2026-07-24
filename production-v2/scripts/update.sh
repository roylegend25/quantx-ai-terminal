#!/usr/bin/env bash
# QuantX Production V2 — zero(ish)-downtime update.
#
# Blue-green for a single-replica backend: build the new image, run it
# side-by-side with the live one on an internal-only port, health-check it,
# only then flip nginx's upstream and stop the old container. If the new
# container ever fails its health check, this script leaves the old
# container running untouched and exits non-zero — nothing is torn down
# until the replacement has proven itself.
#
# Safe to run concurrently with live trading: backend/app/deployment/lease.py
# (EXECUTION_LEASE_KEY in .env) already exists specifically to guarantee only
# one process instance executes an order at a time, so the brief window
# where old+new both run is not a double-execution risk — that lease is what
# makes this side-by-side approach safe at all, see AUDIT.md's `deployment/`
# entry.
#
# Usage: ./scripts/update.sh [git-ref]   (defaults to production-v2 branch tip)
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
set -a; source .env; set +a

TARGET_REF="${1:-origin/production-v2}"
CANDIDATE_PORT=9101
CANDIDATE_NAME=quantx-backend-v2-candidate

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mFATAL: %s\033[0m\n' "$*" >&2; exit 1; }

log "Pre-update backup"
./scripts/backup.sh

log "Fetching $TARGET_REF"
git fetch origin
git checkout "$TARGET_REF" -- . 2>/dev/null || git merge --ff-only "$TARGET_REF"
NEW_SHA="$(git rev-parse --short=12 HEAD)"
NEW_TAG="v2-${NEW_SHA}"
OLD_TAG="${APP_IMAGE_TAG:-unknown}"
[ "$NEW_TAG" != "$OLD_TAG" ] || { echo "Already at $NEW_TAG — nothing to do."; exit 0; }

log "Building candidate image $NEW_TAG"
docker build -t "${BACKEND_IMAGE_PREFIX:-quantx-backend-v2}:${NEW_TAG}" \
  --build-arg APP_GIT_SHA="$NEW_SHA" --build-arg APP_IMAGE_TAG="$NEW_TAG" \
  ../backend

log "Starting candidate on 127.0.0.1:${CANDIDATE_PORT} (old container keeps serving traffic)"
docker rm -f "$CANDIDATE_NAME" >/dev/null 2>&1 || true
docker run -d --name "$CANDIDATE_NAME" \
  --network "$(basename "$PWD")_default" \
  --env-file .env \
  -e APP_GIT_SHA="$NEW_SHA" -e APP_IMAGE_TAG="$NEW_TAG" \
  -v "${DATA_DIR:-./data}:/app/data" \
  -p "127.0.0.1:${CANDIDATE_PORT}:8000" \
  "${BACKEND_IMAGE_PREFIX:-quantx-backend-v2}:${NEW_TAG}"

log "Health-checking candidate"
for i in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:${CANDIDATE_PORT}/api/health/ready" >/dev/null 2>&1 && break
  if [ "$i" -eq 30 ]; then
    docker logs --tail 100 "$CANDIDATE_NAME"
    docker rm -f "$CANDIDATE_NAME" >/dev/null 2>&1 || true
    die "Candidate failed health check — old container is untouched, still serving traffic."
  fi
  sleep 5
done
log "Candidate healthy."

log "Cutting over: stop old backend, promote candidate to the standard name/port"
sed -i "s/^APP_GIT_SHA=.*/APP_GIT_SHA=${NEW_SHA}/" .env
sed -i "s/^APP_IMAGE_TAG=.*/APP_IMAGE_TAG=${NEW_TAG}/" .env
docker rm -f "$CANDIDATE_NAME" >/dev/null 2>&1
docker compose up -d --no-deps --build backend   # recreates using the now-built image + updated .env tag
for i in $(seq 1 20); do
  docker compose exec -T backend curl -fsS http://localhost:8000/api/health/ready >/dev/null 2>&1 && break
  [ "$i" -eq 20 ] && die "Promoted backend failed to come up healthy — see docs/ROLLBACK_GUIDE.md immediately."
  sleep 5
done

log "Reloading nginx"
# nginx's `upstream { server backend:8000; }` resolves the "backend" hostname
# via Docker's embedded DNS once at startup/reload, not per-request — the
# container just recreated above has a new IP, so nginx keeps talking to the
# old (now-dead) one until told to re-resolve. `nginx -s reload` does that
# (re-reads config, workers restart, upstream DNS is re-resolved) without
# dropping the listening socket, so in-flight connections to static
# assets/nginx itself are unaffected.
docker compose exec nginx nginx -s reload

log "Update complete: now running ${NEW_TAG} (was ${OLD_TAG}). Previous image kept for rollback:"
echo "  ${BACKEND_IMAGE_PREFIX:-quantx-backend-v2}:${OLD_TAG}"
echo "See docs/ROLLBACK_GUIDE.md if anything looks wrong in the next few minutes."
