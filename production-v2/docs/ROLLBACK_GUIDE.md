# Rollback Guide

## When to roll back vs. forward-fix

Roll back immediately (don't wait for a fix) if: the resolver/scheduler is throwing on every
cycle, Binance execution is erroring, health checks are failing, or trading has auto-halted
(`safety_halt.py`) after the update. Forward-fix instead only for cosmetic/UI issues that don't
touch the money path.

## Fast rollback (previous image, seconds)

`scripts/update.sh` never deletes the previous image — it's still on disk, tagged with its own
`APP_IMAGE_TAG`. To go back to it:

```bash
cd /opt/quantx/production-v2
grep APP_IMAGE_TAG .env          # confirm this is the BAD tag currently running
PREV_TAG=<the tag update.sh printed as "was ..." when it last ran>

sed -i "s/^APP_IMAGE_TAG=.*/APP_IMAGE_TAG=${PREV_TAG}/" .env
# APP_GIT_SHA should match — check `docker image inspect` labels if unsure:
docker inspect "quantx-backend-v2:${PREV_TAG}" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
# set APP_GIT_SHA in .env to that value too

docker compose up -d --no-deps backend
curl -fsS https://<domain>/api/health/ready
```
This is the same "candidate cutover" mechanism in reverse — old image already exists locally, so
there's no rebuild, just a container swap (few seconds).

## If the bad image was already pruned

Rebuild from the last-known-good commit instead:
```bash
git log --oneline -20                 # find the last-known-good commit on production-v2
git checkout <good-sha>
docker build -t quantx-backend-v2:rollback-<good-sha> --build-arg APP_GIT_SHA=<good-sha> ../backend
sed -i "s/^APP_IMAGE_TAG=.*/APP_IMAGE_TAG=rollback-<good-sha>/" .env
sed -i "s/^APP_GIT_SHA=.*/APP_GIT_SHA=<good-sha>/" .env
docker compose up -d --no-deps backend
git checkout production-v2   # return the working tree to the branch tip afterward
```

## Database rollback (only if the bad deploy also corrupted data — rare)

Restoring `paper.db` from before the bad deploy is destructive to everything written since (real
trades, resolved predictions) — treat this as a last resort, not step one, and see
`docs/DISASTER_RECOVERY.md` for the full procedure and its consequences.

```bash
docker compose stop backend
LATEST_GOOD_BACKUP=/opt/quantx/backups/<timestamp-before-the-bad-deploy>
cp "$LATEST_GOOD_BACKUP/paper.db" /opt/quantx/data/paper.db
docker compose up -d backend
```

## After any rollback

1. Confirm `TRADING_MODE`/`BINANCE_LIVE_ENABLED` in `.env` are what you expect — a rollback should
   never silently change trading mode, but verify anyway before assuming it's safe to let the
   scheduler run unattended.
2. `docker compose logs -f backend` for several minutes.
3. File/track why the bad deploy passed `update.sh`'s health check but still needed a rollback —
   that's a gap in the health check, not just a one-off bad deploy, and should get a stronger
   check added (e.g. a resolver-cycle-succeeded check, not just process-liveness).
