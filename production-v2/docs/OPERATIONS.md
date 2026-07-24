# Operations Reference

## VM sizing

| Tier | Machine type | Disk | When |
|---|---|---|---|
| **Recommended start** | `e2-standard-2` (2 vCPU, 8GB) | 50GB pd-ssd | Default in `docs/DEPLOYMENT_GUIDE.md`. Comfortably covers the single-container backend (asyncio, not CPU-parallel — Active Drive V2's vote computation and the scheduler/resolver loops are lightweight, I/O-bound on Binance/data-provider HTTP calls, not CPU-bound) plus nginx plus Redis. |
| **If image stays full-size** | same, but disk to 60–80GB | The current image bundles the full ML/research toolchain (`ARCHITECTURE.md` "Image size") — each build's layers plus 2 kept-for-rollback images plus SQLite backups adds up. The existing dev VM has hit this wall before (see the disk-preflight cleanup logic already in `scripts/deploy-production.sh` at the repo root) — start with more headroom on V2 rather than repeating that incident. |
| **Scale up if** | `e2-standard-4` | 80GB+ | You add symbols beyond BTCUSDT/ETHUSDT, shorten scheduler/resolver intervals, or do the requirements-trimming follow-up and it turns out something else was CPU-bound. |
| **Do not undersize** | avoid `e2-small`/`e2-micro` | — | The full image's build step alone has needed several GB of transient headroom during layer extraction (documented root-cause in the existing deploy script's comments) — a burst OOM/disk-full mid-build is a worse failure mode than a bit of wasted spend. |

## Required ports

| Port | Direction | Purpose |
|---|---|---|
| 22/tcp | inbound, restricted CIDR | SSH |
| 80/tcp | inbound, public | HTTP → HTTPS redirect + Let's Encrypt ACME challenge |
| 443/tcp | inbound, public | HTTPS (nginx) |
| 127.0.0.1:9100 | loopback only | backend, direct debug access (`docker-compose.yml`) |
| 6379 (redis), 8000 (backend) | internal Docker network only | never published to the host or internet |

## Required environment variables

Full reference lives in `.env.example` with inline comments; the load-bearing ones:

| Variable | Required | Notes |
|---|---|---|
| `SECRET_KEY` | yes | JWT signing — random, 48+ bytes |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` | yes | Dashboard login — bcrypt hash, never the raw password |
| `CREDENTIAL_ENCRYPTION_KEY` | yes | Fernet key, encrypts Binance credentials at rest |
| `PUBLIC_APP_URL` / `CORS_ALLOWED_ORIGINS` | yes | Must match your V2 domain exactly (scheme + host) |
| `DOMAIN` / `LETSENCRYPT_EMAIL` | yes | Deploy-time only (nginx/certbot), not read by the Python app |
| `PAPER_DATABASE_URL` | yes (has a working default) | SQLite path — leave as the `.env.example` default unless you know why you're changing it |
| `REDIS_URL` | yes (has a working default) | Points at the `redis` service by Docker Compose DNS |
| `TRADING_MODE` | yes | `paper` until you deliberately go live |
| `BINANCE_LIVE_ENABLED` | yes | Master lock — `false` until "Going live" below is complete |
| `ENABLED_SYMBOLS` / `BINANCE_ALLOWED_SYMBOLS` | yes | Comma-separated, keep these two consistent |
| `ENABLE_MLOPS_SCHEDULER` / `AUTO_RETRAIN` | recommended `false` | See `ARCHITECTURE.md` Day-0 hardening |
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | optional | Read-only monitoring pair |
| `BINANCE_LIVE_API_KEY` / `BINANCE_LIVE_API_SECRET` | optional, sensitive | Only set once going live (see below) |
| `COINGLASS_API_KEY`, `TELEGRAM_*`, `DISCORD_WEBHOOK_URL` | optional | OPTIONAL-tier integrations per `AUDIT.md` |

## Required secrets (never commit, never log)

1. `SECRET_KEY` — JWT signing key.
2. `ADMIN_PASSWORD_HASH` — bcrypt hash of the dashboard admin password.
3. `CREDENTIAL_ENCRYPTION_KEY` — Fernet key encrypting stored exchange credentials.
4. `BINANCE_API_KEY` / `BINANCE_API_SECRET` — read-only Binance key (no withdrawal/transfer scope).
5. `BINANCE_LIVE_API_KEY` / `BINANCE_LIVE_API_SECRET` — write-capable Binance key, provisioned only
   when going live; keep this pair out of `.env` entirely until then.
6. `LETSENCRYPT_EMAIL` — not sensitive, but real (Let's Encrypt expiry notices go here).

All of the above live only in `.env` (`chmod 600`, never committed — confirm `.gitignore` covers
it, matching the root repo's existing `.env` handling) and in the `.env` copy inside each dated
backup directory (also `chmod 600`, see `docs/BACKUP_GUIDE.md`).

## Health check commands

```bash
curl -fsS http://127.0.0.1:9100/api/health/live      # process is up
curl -fsS http://127.0.0.1:9100/api/health/ready      # DB + dependencies reachable
curl -fsS http://127.0.0.1:9100/api/health/status     # detailed component status
curl -fsS http://127.0.0.1:9100/api/metrics           # Prometheus metrics
docker compose ps                                      # container-level health (healthcheck: blocks in docker-compose.yml)
```
From outside the VM, same paths over HTTPS: `curl -fsS https://<domain>/api/health/ready`.

## Debug commands

```bash
docker compose logs -f backend                 # follow live logs
docker compose logs --since 1h backend | grep -i error
docker compose exec backend python3 -c "from app.db.session import SessionLocal; s=SessionLocal(); print(s.execute('SELECT 1').scalar())"
docker compose exec redis redis-cli info stats
docker compose exec backend curl -s http://localhost:8000/api/health/status | python3 -m json.tool
docker stats --no-stream                       # CPU/memory per container, right now
df -h /opt/quantx                              # disk headroom (see VM sizing above)
sudo journalctl -u quantx-v2.service --since today
sudo journalctl -u quantx-v2-backup.service -n 50
sudo journalctl -u quantx-v2-renew.service -n 50
```

## Upgrade commands

Covered in full in `docs/UPDATE_GUIDE.md`; the one-liner for the common case:
```bash
cd /opt/quantx/production-v2 && ./scripts/update.sh
```

## Zero-downtime deployment process

`scripts/update.sh` implements this (see its header comment for the full rationale): build the new
backend image, run it side-by-side with the live one on an internal-only port, health-check it,
and only then stop the old container and promote the new one. The brief window where both
containers are alive is safe specifically because `EXECUTION_LEASE_KEY`/`deployment/lease.py`
already serializes order execution across processes (built for exactly this kind of transition,
per `AUDIT.md`'s `deployment/` package entry) — so there's no double-execution risk during the
overlap. Nginx itself is never restarted for a backend-only update, so client connections to
static assets/other pages are never interrupted; only backend API calls made in the exact
sub-second window of the container swap could see a single failed request, which the frontend
already retries (standard fetch failure handling).

## Going live (Binance real trading) — do this deliberately, not by accident

1. Run in `paper` mode for a meaningful stretch first and confirm Daily Accuracy / resolver metrics
   look sane — that's the entire point of `TRADING_MODE=paper` as the default.
2. Provision a **separate**, write-capable Binance API key (`BINANCE_LIVE_API_KEY`/`_SECRET`) with
   the minimum scope Binance allows for futures trading — never reuse the read-only monitoring key.
3. Set `BINANCE_FUTURES_TESTNET=false` only after testnet dry-runs look correct.
4. Review `BINANCE_MAX_NOTIONAL_PER_TRADE`, `BINANCE_MAX_DAILY_LOSS_USDT`,
   `BINANCE_MAX_LEVERAGE`, `BINANCE_ALLOWED_SYMBOLS` in `.env` — these are real financial limits,
   not placeholders.
5. Flip `BINANCE_LIVE_ENABLED=true`, redeploy (`docker compose up -d --no-deps backend`).
6. `BINANCE_LIVE_ENABLED` is only one of three independent gates (`AUDIT.md`/`core/config.py`
   comment: also `TradingControl.mode` in the DB, and the short-lived live-authorization lease) —
   no single flag flip actually places a real order, by design.
