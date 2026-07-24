# Disaster Recovery Guide

Scenarios, in order of likelihood, each with the recovery procedure and its real consequences —
read the consequences before running anything here, some of these are destructive.

## 1. VM is unreachable / GCP zone issue

**Recovery:** provision a new VM and redeploy from scratch — this is exactly what `deploy.sh` is
for, so recovery time is bounded by "how long deploy.sh takes", not by any manual rebuild process.

```bash
# from your workstation, new VM (see docs/DEPLOYMENT_GUIDE.md step 1, pick a new VM_NAME)
gcloud compute instances create quantx-prod-v2-recovery ...
gcloud compute ssh quantx-prod-v2-recovery ...
# on the new VM:
curl -fsSL https://raw.githubusercontent.com/roylegend25/quantx-ai-terminal/production-v2/production-v2/deploy.sh -o deploy.sh
chmod +x deploy.sh && ./deploy.sh
```
Then restore the database from the most recent off-box backup copy (`docs/BACKUP_GUIDE.md`) —
data written between the last backup and the outage is lost; this is the real cost of the daily
backup cadence, and the reason to wire up the off-box copy step described in that guide before you
actually need it.

```bash
docker compose stop backend
cp <restored-backup>/paper.db /opt/quantx/data/paper.db
cp <restored-backup>/.env /opt/quantx/production-v2/.env   # only if the old .env is also lost
docker compose up -d backend
```
Update DNS to the new VM's IP; if HTTPS was already issued for this domain recently, Let's
Encrypt's rate limits (5 duplicate certs/week per domain) are rarely an issue for a single recovery.

## 2. Database corruption (SQLite reports errors, app crashes on a query)

```bash
docker compose stop backend
sqlite3 /opt/quantx/data/paper.db "PRAGMA integrity_check;"   # confirm it's actually corrupt, not a transient lock
```
If corrupt: restore the most recent backup (accept the data-loss window since the last backup —
there's no partial-repair path worth trusting for a trading ledger):
```bash
LATEST=$(ls -1d /opt/quantx/backups/*/ | tail -1)
cp "$LATEST/paper.db" /opt/quantx/data/paper.db
docker compose up -d backend
curl -fsS http://127.0.0.1:9100/api/health/ready
```

## 3. Bad deploy broke production (code, not data)

This is `docs/ROLLBACK_GUIDE.md`, not this file — no data recovery needed, just an image swap.

## 4. Binance API keys compromised / suspicious activity

**Immediate, from the Binance UI (not this VM):** revoke/rotate the API key at binance.com first —
a leaked key is a Binance-account-level problem, no amount of VM-side action fixes it.

**Then, on the VM**, stop the app from attempting to trade with the now-dead key rather than
generating a wall of auth-failure errors:
```bash
sed -i 's/^BINANCE_LIVE_ENABLED=.*/BINANCE_LIVE_ENABLED=false/' /opt/quantx/production-v2/.env
docker compose up -d --no-deps backend
```
Verify no open positions were left unprotected (`binance_protection_capability`,
`binance_block_new_trades_if_unprotected` in `AUDIT.md`'s table list are exactly the guardrails
here) via the Binance page / Binance's own UI directly before re-enabling anything. Issue new keys,
update `.env`, redeploy via `scripts/update.sh` semantics (env-only, so just
`docker compose up -d --no-deps backend` again) once confirmed safe.

## 5. Complete data loss with no usable backup (worst case)

There is no way to recover trade/prediction history that was never backed up — this scenario's
"recovery" is `deploy.sh` on a fresh VM with an empty database, which just means production starts
its ledger from zero. This is the scenario the off-box backup copy in `docs/BACKUP_GUIDE.md` exists
to prevent; if you're reading this because it already happened, that's the first thing to fix
before anything else.

## Recovery time / recovery point targets (for planning, not enforced by any code here)

- **RTO** (how long until a fresh VM is serving traffic again): bounded by `deploy.sh`'s runtime —
  typically 15–30 minutes including DNS propagation and the first Let's Encrypt issuance.
- **RPO** (how much data can be lost): bounded by backup cadence — 24h with the default daily
  timer, tighter if you reduce `OnCalendar` in `systemd/quantx-v2-backup.timer` or add an off-box
  sync (`docs/BACKUP_GUIDE.md`).
