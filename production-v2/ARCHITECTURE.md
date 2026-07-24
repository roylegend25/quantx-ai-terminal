# Phase 2 — Clean Production V2 Architecture

## Build approach (decision recorded here for future readers)

Two ways to get to "clean production" were considered:

1. **Physical code extraction** — copy only CORE files into a new minimal backend package.
   Rejected for the *first* cutover because `AUDIT.md` found real entanglement: `api/prediction.py`
   (prediction generation) and `trading/execution_router.py` (live order routing) each import a mix
   of CORE and non-CORE modules. Extracting cleanly means rewriting those two files and re-testing
   the money path before the first production deploy — real value, but a separate, higher-risk
   project.
2. **Config-level clean deploy (chosen)** — ship the existing, working codebase unmodified into
   the V2 image. Nothing on the money path is touched. "Clean" is enforced at three levels instead
   of by deleting code:
   - **Infra**: drop the dead Postgres container (§4 of `AUDIT.md` — nothing ever connects to it),
     keep SQLite + Redis only.
   - **Network**: nginx on the V2 host only proxies the 8 required pages/routes; RESEARCH routes
     (`/api/ml`, `/api/backtest`, `/api/research*`, `/api/stress`, `/api/models`, `/api/learning`)
     return `404` at the edge.
   - **Process**: two small, additive, feature-flagged changes (see "Day-0 hardening" below) — not
     deletions — turn off the one background job that has no off-switch today.

   Physical extraction remains a good follow-up once the entangled files have dedicated tests; this
   audit is written so that work can start from an accurate map instead of a fresh investigation.

## Day-0 hardening (additive only, ~30 minutes, safe to skip if you want *zero* code touched)

Both changes are pure additions (new env-gated `if`), never a deletion or rewrite of existing logic:

```python
# backend/app/core/config.py — add one field
enable_mlops_scheduler: bool = True   # default True preserves current dev behavior

# backend/app/main.py — wrap the one job with no existing off-switch
if settings.enable_mlops_scheduler:
    start_mlops_scheduler()
```
Set `ENABLE_MLOPS_SCHEDULER=false` in the V2 `.env`. If you'd rather ship with zero code diff on
day 0, leave it — the job is harmless (just background CPU/DB writes to `mlops_*` tables that
nothing in the V2 UI reads), and add the flag in the first follow-up PR instead.

**Image size — verified, not assumed:** `backend/app/main.py` unconditionally imports
`app.api.{ml,research,backtest,stress,models,research_lab,learning}` at module level (every router
is registered eagerly, there's no lazy-loading), and those modules transitively pull in
`ml_lab/algorithms.py`, `ml_lab/hpo.py`, `ml_lab/keras_models.py`, `ml/train_xgboost.py`, etc. —
which is why `tensorflow`/`xgboost`/`lightgbm`/`catboost`/`optuna` are in `requirements.txt` at all
(confirmed: nothing on the Active Drive V2 / paper / Binance / accuracy path imports them). This
means nginx blocking `/api/ml`, `/api/research*` etc. **narrows network exposure but does not
shrink the image** — the process still needs those packages installed to boot, the same ~5.7 GB
image running today. Actually slimming the image is a real, separate, well-scoped follow-up
(wrap those 7 router imports in `main.py` in the same `if settings.enable_research_routes:` pattern
as the mlops flag above, then ship a trimmed `requirements-prod.txt`) — worth doing, but out of
scope for a same-day cutover, so V2 Day-0 ships the current image unchanged.

## Backend (single container, matches current runtime shape)

The current app is already a monolith: Active Drive V2, the scheduler, the resolver, paper
trading, Binance trading, the candle collector, and the REST API all run **in one FastAPI process**
as asyncio background tasks (`main.py` startup event) — there are no separate worker containers to
invent. V2 keeps that shape:

```
backend (container: quantx-backend-v2)
├── REST API           (FastAPI, all CORE routers from AUDIT.md §2 only need be *reachable*
│                        through nginx — RESEARCH/OPTIONAL routers can stay compiled in, unused)
├── Active Drive V2    (backend/app/decision_engine/*)
├── Scheduler          (backend/app/trading/scheduler.py + engine/trading_engine.py)
├── Resolver           (backend/app/decision_engine/scheduler.py + resolver.py)
├── Paper Trading      (backend/app/execution/execution_engine.py)
├── Binance Trading    (backend/app/exchanges/binance_*.py + trading/execution_router.py)
├── Candle Collector   (backend/app/data_sources/{downloader,normalizer,scheduler,cache}.py)
└── Accuracy Engine    (backend/app/analytics/{daily_performance,candle_retention}.py)
```

## Frontend (static SPA, nginx-served)

Same Vite/React build. The 8 required pages already exist as lazy-loaded route chunks (confirmed
in `App.tsx` — `lazy(() => import(...))`), so RESEARCH/OPTIONAL page bundles are already
code-split and never fetched unless a user navigates to them directly by URL — which nginx now
blocks (see `nginx/nginx.conf`). No frontend rebuild is required to achieve this; it's an nginx
routing decision, not a Vite build decision.

## Infrastructure

```
┌─────────────────────────────────────────────────────────┐
│                      GCP VM (Prod V2)                    │
│  ┌───────────┐   :443/:80                                │
│  │  nginx    │───────────────┐                            │
│  │ (container)│               │                            │
│  └───────────┘               ▼                            │
│        │              ┌──────────────┐     ┌───────────┐  │
│        │ static files │   backend    │────▶│  redis    │  │
│        └─────────────▶│  (FastAPI)   │     │(container)│  │
│                        │  container   │     └───────────┘  │
│                        └──────┬───────┘                    │
│                               │ bind mount                 │
│                               ▼                            │
│                     /opt/quantx/data/*.db (SQLite, WAL)     │
│                     — backed up on a systemd timer —        │
└─────────────────────────────────────────────────────────┘
```

- **SQLite** (not Postgres) — matches what the code has always actually used, WAL mode, single
  `paper.db` file under a host-persistent bind mount.
- **Redis** — cache + rate limiting, no persistence required (ephemeral, rebuildable).
- **Docker Compose** — `backend`, `redis`, `nginx` — 3 services, down from 3+1 dead one today.
- **Nginx** — containerized (not host-installed) so the whole stack is defined in
  `docker-compose.yml` per Phase 4's reproducibility requirement; terminates TLS via a mounted
  `certbot` cert volume.

## Repository layout of this deliverable

```
production-v2/
├── README.md              entry point / quick start
├── AUDIT.md                Phase 1 classification (this repo, evidence-based)
├── ARCHITECTURE.md         this file
├── deploy.sh               Phase 3/4 bootstrap script — the only script a fresh VM needs
├── docker-compose.yml       backend + redis + nginx, SQLite volume
├── .env.example             every required env var, no secrets filled in
├── nginx/nginx.conf          CORE-routes-only reverse proxy config
├── systemd/
│   ├── quantx-v2.service         docker compose up, restart policy, boot ordering
│   ├── quantx-v2-backup.service   one-shot SQLite backup
│   └── quantx-v2-backup.timer     daily schedule for the backup service
└── docs/
    ├── DEPLOYMENT_GUIDE.md   Phase 3, the 18 steps, in order, with real commands
    ├── GITHUB_WORKFLOW.md    Phase 5, branch → environment promotion
    ├── UPDATE_GUIDE.md
    ├── ROLLBACK_GUIDE.md
    ├── BACKUP_GUIDE.md
    ├── DISASTER_RECOVERY.md
    └── OPERATIONS.md          VM sizing, ports, env vars, secrets, health/debug/upgrade commands,
                                 zero-downtime deploy process
```

This directory lives inside the existing repo (`quantx-ai-terminal/production-v2/`) so it ships
through the normal GitHub flow described in `docs/GITHUB_WORKFLOW.md` — the new VM's `deploy.sh`
clones the repo and `cd`s straight into `production-v2/` to run `docker compose`. Nothing here
touches `docker-compose.yml`, `.env`, or `nginx/default.conf` at repo root — those remain exactly
as they are, running the current (dev, going forward) VM untouched.
