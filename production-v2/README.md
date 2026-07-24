# QuantX — Production V2

A clean, from-scratch production deployment of Active Drive V2 — the prediction/execution engine,
not the whole `quantx-ai-terminal` monorepo. Built to run on a **brand-new** GCP VM, independent of
the existing box (which becomes Development going forward). Nothing here migrates, deletes, or
touches that existing VM.

Read `AUDIT.md` first if you want to know *why* something is (or isn't) in this build — every
CORE/OPTIONAL/RESEARCH/LEGACY call is backed by an actual import-chain check against the repo, not
a guess from file names. `ARCHITECTURE.md` explains the build approach this kit uses (config-level
clean deploy — ship the working codebase unmodified, enforce "clean" via infra/network/process
config — and why the alternative, physically extracting a minimal codebase, was set aside for a
later follow-up).

## Quick start

```bash
# from your workstation — create the VM (see docs/DEPLOYMENT_GUIDE.md for the full gcloud commands)
gcloud compute instances create quantx-prod-v2 --zone=... --machine-type=e2-standard-2 ...

# SSH in, then on the VM:
curl -fsSL https://raw.githubusercontent.com/roylegend25/quantx-ai-terminal/production-v2/production-v2/deploy.sh -o deploy.sh
chmod +x deploy.sh
./deploy.sh              # stops once, asking you to fill in .env
nano .env                 # fill in every CHANGE-ME value — see docs/OPERATIONS.md "Required secrets"
./deploy.sh               # resumes and finishes the deploy
```
That's the whole reproducibility contract (Phase 4): an empty VM plus this repo's
`production-v2/deploy.sh` + `docker-compose.yml` + `.env.example` becomes a working server. The
only manual step, ever, is filling in secrets.

## What's actually running

```
                    ┌─────────── nginx (container, :80/:443) ───────────┐
                    │  serves frontend/dist · proxies /api, /ws          │
                    │  returns 404 on RESEARCH routes at the edge        │
                    └──────────────────────┬──────────────────────────┬─┘
                                            │                          │
                                            ▼                          │
                    ┌─────────── backend (container) ──────────────┐   │
                    │  REST API · Active Drive V2 · Scheduler       │   │
                    │  Resolver · Paper Trading · Binance Trading   │   │
                    │  Candle Collector · Accuracy Engine           │◀──┘
                    └───────┬────────────────────────────┬─────────┘
                             │                            │
                             ▼                            ▼
                     SQLite (bind mount,             Redis (container,
                     WAL mode, backed up              cache + rate limit,
                     nightly)                          ephemeral)
```
See `ARCHITECTURE.md` for the full diagram and reasoning, `AUDIT.md` for the file-by-file basis.

## Folder structure (this kit)

```
production-v2/
├── README.md               you are here
├── AUDIT.md                 Phase 1 — CORE/OPTIONAL/RESEARCH/LEGACY classification, with evidence
├── ARCHITECTURE.md          Phase 2 — clean architecture, build-approach decision, diagram
├── deploy.sh                Phase 3/4 — first-boot bootstrap (idempotent, safe to re-run)
├── docker-compose.yml        backend + redis + nginx, SQLite volume, no Postgres
├── .env.example              every env var the app/scripts read, no secrets filled in
├── nginx/
│   ├── Dockerfile             multi-stage: builds frontend, serves it + reverse-proxies
│   └── nginx.conf              CORE-routes-only, RESEARCH routes return 404 at the edge
├── systemd/                   boot ordering, daily backup timer, cert-renewal timer
├── scripts/
│   ├── backup.sh               SQLite online backup (safe under WAL, no data loss on a live DB)
│   ├── renew-cert.sh           Let's Encrypt renewal
│   └── update.sh                zero(ish)-downtime backend update (blue-green single-replica)
└── docs/
    ├── DEPLOYMENT_GUIDE.md      Phase 3, all 18 steps, real gcloud/bash commands
    ├── GITHUB_WORKFLOW.md       Phase 5, branch → environment promotion, "never dev on prod"
    ├── UPDATE_GUIDE.md
    ├── ROLLBACK_GUIDE.md
    ├── BACKUP_GUIDE.md
    ├── DISASTER_RECOVERY.md
    └── OPERATIONS.md            VM sizing, ports, env vars, secrets, health/debug/upgrade commands
```

## The 8 pages this build serves

Dashboard (incl. Decision Engine reasoning), Predictions, Paper Trading, Binance, Daily Accuracy,
Bot Settings (incl. Risk), System Health. Everything else in the existing frontend
(`AUDIT.md` §5 — backtesting, research lab, model center, self-learning, stress test, etc.) is
still compiled into the app (unmodified codebase, per the config-level approach) but unreachable
in this deployment: its nginx routes 404 and its nav entries are absent, so a real user never sees
it.

## What is deliberately NOT in this build

Postgres (dead in the existing repo too — nothing ever connects to it, see `AUDIT.md` §4), the
ML/research/backtest/stress/model-lab surface (RESEARCH per `AUDIT.md`), Trading Horizon as a
decision *authority* (superseded — Active Drive V2 is the sole authority, though two of its utility
submodules remain load-bearing dependencies of live execution, see `AUDIT.md`'s `trading_horizon/`
entry), and the QuantX Classic / V1 engine (already fully separated per the existing repo's own
`.env.example` comments).
