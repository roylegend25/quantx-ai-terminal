# Update Guide

Production V2 never receives a `git pull` run by hand on the box (see `docs/GITHUB_WORKFLOW.md`
rule 2). Every update goes through `scripts/update.sh`, which is also the zero-downtime deployment
process referenced in `docs/OPERATIONS.md`.

## Normal update (promoted code already on the `production-v2` branch)

```bash
ssh <prod-v2-vm>
cd /opt/quantx/production-v2
./scripts/update.sh
```

This:
1. Takes a pre-update SQLite backup (`scripts/backup.sh`).
2. Builds the new backend image from `origin/production-v2`'s tip.
3. Runs it side-by-side with the currently-live container on an internal-only port
   (`127.0.0.1:9101`) — the old container keeps serving all real traffic throughout.
4. Health-checks the candidate against `/api/health/ready`.
5. Only if that passes: stops the old container and promotes the candidate to the standard
   name/port. If it fails, the candidate is discarded and the old container is never touched.

Nginx and Redis are not touched by a normal update — only the backend image changes.

## Updating to a specific commit/tag

```bash
./scripts/update.sh origin/production-v2       # default
./scripts/update.sh <commit-sha>
./scripts/update.sh v2.3.0                     # if you tag releases
```

## Updating nginx config or the frontend

These aren't hot-swappable the same way (nginx's own image has to be rebuilt):
```bash
docker compose build nginx
docker compose up -d nginx     # a few hundred ms of connection reset while nginx restarts
```
For a config-only change, prefer editing `production-v2/nginx/nginx.conf`, rebuilding, and
reloading rather than `up -d` where possible:
```bash
docker compose build nginx
docker compose up -d --no-deps nginx
```

## After every update

```bash
curl -fsS https://<domain>/api/health/ready
docker compose ps
docker compose logs --tail 100 backend
```
Watch `docker compose logs -f backend` for a few minutes — specifically for resolver/scheduler
errors, since those loops (paper/Binance execution, prediction resolution) restart their internal
state on process boot. If anything looks wrong, go straight to `docs/ROLLBACK_GUIDE.md` — don't
try to forward-fix on production.

## Updating dependent infra (systemd units, backup timer schedule, etc.)

These live in `production-v2/systemd/` and are only re-installed when you explicitly re-run that
part of `deploy.sh` (it's idempotent — safe to re-run in full even after first boot):
```bash
sudo cp systemd/*.service systemd/*.timer /etc/systemd/system/
sudo sed -i "s#__INSTALL_DIR__#/opt/quantx#g" /etc/systemd/system/quantx-v2*.service
sudo systemctl daemon-reload
sudo systemctl restart quantx-v2-backup.timer quantx-v2-renew.timer
```
