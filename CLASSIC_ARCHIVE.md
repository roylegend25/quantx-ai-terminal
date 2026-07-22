# QuantX Classic (Legacy Decision Engine) — Archived Snapshot

> **⚠️ ARCHIVED / LEGACY.** This branch (`archive/quantx-classic`) is a preservation
> snapshot of the repository from the point immediately before Active Drive V1
> and the legacy ensemble strategy were descoped from the production
> decision path in Premium X Dark / `main`. It exists so the legacy engine
> remains recoverable and independently runnable, without it continuing to
> occupy the production critical path. **Do not deploy this branch as
> production.** Production is `main`.

## What "QuantX Classic" actually is in this codebase

Investigation before this archive was created (see the accompanying Stage 2
report in the parent conversation) established that there is **no separate
"QuantX Classic" frontend** — the "Classic" vs "Premium Dark" split is a
client-side theme toggle over the same page components, and Premium Dark
depends on those shared components for most of its pages. That frontend
toggle is therefore **not** part of this archive and was left in place in
`main`.

What genuinely is a separate, legacy, non-authoritative system is the
**decision-engine layer** this archive preserves:

- `backend/app/decision_engine/v1.py` — `ActiveDriveV1Adapter`, a thin
  pass-through wrapper around the legacy ensemble's own decision (version
  `1.0.0`).
- `backend/app/strategy/ensemble.py` — the original indicator/strategy
  ensemble ("Classic") evaluation this repo shipped before Active Drive V2.
- `backend/app/timeframes/multi_timeframe.py` — the 10-timeframe legacy
  consensus calculator built on top of `ensemble.py`, previously invoked
  unconditionally inside `/api/prediction` regardless of which engine was
  authoritative (identified in the Stage 1 performance audit as a major
  source of unnecessary latency in the V2 request path).

## Exact source commit

This archive branch was created from:

```
commit 4bc4055 (fix/decision-execution-pipeline-link, the commit running in
production at the time of this archive)
"Fix Decision/Execution Pipeline contradiction: unify timeframe resolution
and add authoritative pipeline state API"
```

Run `git log -1 4bc4055` on this branch to confirm.

## Architecture overview

Classic's decision path (still fully intact in this snapshot):

```
candles (Binance REST)
  -> app/quant/indicators.compute_features()
  -> app/strategy/ensemble.evaluate()          # the "Classic" ensemble
  -> app/decision_engine/v1.py ActiveDriveV1Adapter.evaluate()
  -> persisted via app/decision_engine/ledger.py (same tables V2 uses)
```

`app/timeframes/multi_timeframe.py` runs the same `ensemble.evaluate()`
independently across 10 timeframes (`1m,3m,5m,15m,30m,1h,4h,1d,1w,1M`) to
build a cross-timeframe "consensus" — this is what `/api/prediction` used
to call on every request to compute a confidence nudge, regardless of
whether V1 or V2 was the active engine.

Selecting V1 as the active engine is (and remains, in `main`) reachable via
`PATCH /api/*/decision-engine` and the admin Bot Settings UI
(`frontend/src/components/Trading/DecisionEngineSettings.tsx`), gated by
`ACTIVE_DRIVE_V1_AVAILABLE`. Even with V1 selected, the automated scheduler
never executes V1-driven trades automatically: Trading Horizon's authority
issuance (`app/trading_horizon/authority.py`) hard-requires
`engine == "active_drive_v2"` on every required timeframe decision, so an
automated cycle with V1 active always fails closed to `NO_TRADE`.

## Requirements

Same as the main project at this commit — see `backend/requirements.txt`
and `frontend/package.json`. No additional/different dependencies are
needed to run the legacy engine; it's part of the same FastAPI app.

## Backend setup (to run this archived snapshot standalone)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # fill in SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD_HASH
# Force the legacy engine to be authoritative for this run:
#   DEFAULT_DECISION_ENGINE=active_drive_v1
#   ACTIVE_DRIVE_V1_AVAILABLE=true
#   ACTIVE_DRIVE_V2_ENABLED=false      # optional - fully disables V2 selection
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Frontend build instructions

```bash
cd frontend
npm ci
npm run build
```

The frontend is unchanged from `main` at this commit — no Classic-specific
frontend build exists (see "What QuantX Classic actually is" above).

## Docker Compose deployment

Use the repository's existing `docker-compose.yml` unmodified. To force the
legacy engine, set in `.env` before `docker compose up`:

```
DEFAULT_DECISION_ENGINE=active_drive_v1
ACTIVE_DRIVE_V1_AVAILABLE=true
```

## `.env.example`

See `.env.example` at the repository root (already secret-free — verified,
contains only placeholders). No separate Classic `.env.example` is needed.

## Database migration instructions

No separate migrations exist for the legacy engine — `ActiveDriveDecision`
and `PredictionLedger` rows are shared between V1 and V2 (differentiated by
the `engine` column). Run the same migration path as `main`:

```bash
docker exec quantx-backend python -c "from app.db.init_db import initialize_schema; from app.db.session import engine; initialize_schema(engine)"
```

## Paper-mode startup instructions

Ensure, before starting:

- `TRADING_MODE=paper`
- `BINANCE_LIVE_ENABLED=false` (or unset)
- `BINANCE_FUTURES_TESTNET=true`

Then start normally (see Backend setup above). No real-money order path is
touched by the legacy engine at any point — it shares the same paper
execution provider as V2.

## Nginx deployment example

See `nginx/default.conf` at the repository root — unchanged, serves the
same single frontend build regardless of active engine.

## Health-check commands

```bash
curl -fsS http://127.0.0.1:8000/api/health
```

Expect `"status":"ok"`. Confirm the active engine via:

```bash
curl -fsS -H "Authorization: Bearer <token>" http://127.0.0.1:8000/api/bot/decision-engine
```

## Rollback instructions

This archive is a read-only reference. To "roll back" to running the
legacy engine in an emergency:

1. Checkout this branch (`git checkout archive/quantx-classic`) into a
   separate directory/environment — do not run it against the production
   database unless you understand the shared-table implications above.
2. Set `DEFAULT_DECISION_ENGINE=active_drive_v1` and restart.
3. To return to production, redeploy from `main` as normal
   (`scripts/deploy-production.sh`).

## Test verification (run at archive time)

```
python -m pytest tests/test_ensemble.py tests/test_ensemble_market_context.py \
  tests/test_legacy_neutral_compat.py tests/test_active_drive_v2.py -q
```

Result at archive time: **43 passed**, 0 failed.

---

*This file was added solely to document the archive; it introduces no code
changes relative to the source commit above.*
