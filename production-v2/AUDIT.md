# Phase 1 — Repository Audit & Classification

Source: `quantx-ai-terminal` @ `f63cfdc` (branch `perf-and-cleanup-v2-only`), audited by reading
actual imports and call graphs, not just file/folder names. Every classification below is backed
by a specific piece of evidence (grep output, import chain, DB table usage) so it can be checked
against the live repo at any time — this is a snapshot, re-verify before deleting anything.

Legend: **CORE** required for Active Drive V2 · **OPTIONAL** useful, not required ·
**RESEARCH** experiments/training · **LEGACY** V1/Classic/Horizon/dead code.

> Important nuance found during the audit (this is *why* Phase 2 is a config-level clean deploy,
> not a code extraction — see `ARCHITECTURE.md`): a handful of CORE files are entangled with
> non-core imports. `backend/app/api/prediction.py` (prediction generation) pulls in
> `app.strategy.ensemble`, `app.ml_lab.champion_gate`, and `app.ml.feature_store` alongside the
> Active Drive V2 decision engine. `backend/app/trading/execution_router.py` (live order routing)
> still imports `app.trading_horizon.idempotency` and `app.trading_horizon.sizing` even though
> Trading Horizon is deprecated as a decision *authority* (commit `f63cfdc`: "Consolidate on
> Active Drive V2 as sole prediction authority"). Those two `trading_horizon` submodules are
> therefore CORE-by-dependency even though the rest of `trading_horizon/` is LEGACY.

---

## 1. Backend — `backend/app/` packages

| Package | Class | Evidence |
|---|---|---|
| `decision_engine/` | **CORE** | Active Drive V2 itself. `v2.py`/`sources.py` have zero dependency on `ai/`/`ml/`/`ml_lab/`/`mlops/` — votes are computed from raw feature dict + `risk.settings_repository` only. `scheduler.py` drives the resolver loop; `resolver.py` resolves predictions against `MarketCandle`. |
| `trading/` | **CORE**, minus 2 dead files | Drives paper + Binance execution. `scheduler.py` → `engine/trading_engine.py` → `execution/order_router.py` + `trading/execution_router.py`. `signal_engine.py` and `executor.py` are **0 bytes — dead code (LEGACY)**, safe to delete outright. |
| `execution/` | **CORE** | `execution_engine.py` (paper engine), `order_router.py`, `retry.py`, `slippage.py`, `execution_metrics.py` all sit directly on the live/paper order path. |
| `exchanges/` | **CORE** (Binance only) | `binance*.py` (futures client, spot, rate limiter, snapshot service, errors, time) are wired into `execution_router.py`/`portfolio.py`. `bybit.py`, `coinbase.py`, `okx.py` — **no CORE file imports them** (grep: unreferenced outside their own module) → **LEGACY/OPTIONAL** (kept for future multi-exchange work, not required now). |
| `risk/` | **CORE** | `settings_repository.py` used by `decision_engine/v2.py`, `api/risk.py`, `api/dashboard.py`. `threshold_risk.py` used by risk gate. |
| `monitoring/` | **CORE** | `health.py`, `logging.py`, `metrics.py` used everywhere, including System Health page. `tracing.py` — OPTIONAL (observability nicety, not required for health checks to function). |
| `analytics/` | **CORE** | `daily_performance.py` + `candle_retention.py` back the Daily Accuracy page (`api/daily_accuracy.py`, `api/analytics.py`). `reporting.py` — OPTIONAL (broader report generation, not the accuracy engine itself). |
| `data_sources/` | **CORE** (candle collector), split | `downloader.py`, `normalizer.py`, `symbol_map.py`, `validator.py`, `resolution_providers.py`, `scheduler.py`, `cache.py`, `binance_futures.py` are the actual candle collector (imported 3–9× each). `fear_greed.py`, `funding.py`, `liquidation.py`, `open_interest.py`, `orderbook.py`, `macro.py`, `binance_spot.py` — **0 internal imports found — dead/superseded code (LEGACY)**, replaced by `intelligence/*` equivalents. `coinglass.py`, `cryptoquant.py`, `glassnode.py`, `hyblock.py` — low-usage paid third-party feeds (1–4 importers) → **OPTIONAL**. |
| `intelligence/` | Mixed | `funding.py`, `open_interest.py`, `orderflow.py` → **CORE**, they supply `funding_rate`/`oi_change_pct`/`bid_ask_ratio`/`cvd` that `decision_engine/sources.py::quant_votes()` reads directly. `fear_greed.py`, `news_sentiment.py`, `whale_tracker.py`, `liquidations.py`, `liquidation_heatmap.py`, `hyperliquid_trades.py` → **OPTIONAL** (dashboard enrichment, not consumed by Active Drive V2 voting). |
| `strategy/` | **OPTIONAL** | The legacy ensemble/weighting engine (`ensemble.py`, `breakout.py`, `momentum.py`, `mean_reversion.py`, `trend.py`, `weight_calculator.py`, `manager.py`, `regime.py`). Still imported by `api/prediction.py` (ensemble evaluation alongside Active Drive V2) and `api/paper.py` (`performance_repository`/`rolling_metrics_repository` for legacy performance stats) — **do not delete**, but it is not part of the Active Drive V2 vote path (`decision_engine/sources.py` hardcodes its own vote logic and does not call into `strategy/`). |
| `ai/`, `ml/`, `ml_lab/`, `mlops/` | **RESEARCH** | Full ML train/serve/retrain/experiment stack (LSTM, XGBoost, CatBoost, LightGBM, drift detection, champion gate, HPO, SHAP). Confirmed zero import from `decision_engine/`. `ml_lab.champion_gate` and `ml.feature_store` are imported by `api/prediction.py` for a parallel/legacy ML-scored path, not by Active Drive V2 itself. |
| `research/` | **RESEARCH** | Backtesting lab: Monte Carlo, walk-forward, optimizer, experiment tracker. Not on any live path. |
| `backtest/`, `stress/` | **RESEARCH** | Backtest engine and stress-scenario simulator; only reachable via `api/backtest.py`/`api/stress.py`. |
| `learning/` | **RESEARCH** | `evaluator.py`/`recommender.py`, self-learning report generation only. |
| `features/` | **OPTIONAL** | `engine.py` — a features abstraction only used by `api/data.py`, not by the decision engine's own feature dict. |
| `quant/` | **CORE** | `forecast.py`/`indicators.py` compute the feature dict (`compute_features`) consumed by `api/prediction.py` and ultimately by Active Drive V2. |
| `engine/` | **CORE** | `trading_engine.py` is the scheduler's execution driver (see `trading/scheduler.py`); `state.py`/`events.py` support it. |
| `deployment/` | **CORE** | `maintenance.py` (maintenance-mode marker), `lease.py` (execution lease used by scheduler + execution router), `clock_preflight.py`. Ops plumbing, required at runtime. |
| `timeframes/` | **CORE** | `canonical.py` used throughout prediction/decision code; `multi_timeframe.py` — OPTIONAL (only `api/timeframes.py`). |
| `db/` | **CORE**, with LEGACY migrations | `models.py`, `session.py`, `init_db.py` are CORE. `trading_horizon_migration.py`, `trading_horizon_issuance_migration.py`, `risk_settings_scope_migration.py` are one-time migrations already applied on the current DB — irrelevant to a *fresh* V2 DB (a fresh `init_db.py` create-all already produces the current schema), keep only for historical reference. |
| `trading_horizon/` | **Split** | `authority.py`, `current_authority.py`, `service.py`, `diagnostics.py` = **LEGACY** (superseded decision authority, still referenced only by diagnostics endpoint `api/pipeline.py`). `idempotency.py`, `sizing.py` = **CORE** (imported directly by `trading/execution_router.py` for the live/paper order path — do not remove without replacing the call sites first). |
| `core/` | **CORE** | Config, security, deps, env manager (backs the Bot Settings server-config panel), response_meta. |

## 2. Backend — `backend/app/api/*.py` (REST surface)

Classified from actual imports (see audit transcript); each row is one FastAPI router.

**CORE:** `auth.py`, `dashboard.py`, `prediction.py`, `prediction_results.py`, `analysis.py`,
`daily_accuracy.py`, `analytics.py`, `bot.py`, `bot_trades.py`, `paper.py`, `portfolio.py`,
`trading_control.py`, `exchange.py`, `binance_credentials.py`, `binance_snapshot.py`,
`execution.py`, `risk.py`, `indicator_control.py`, `admin_config.py`, `logs.py`, `market.py`,
`orderbook.py`, `trades.py`, `timeframes.py`, `data.py`, `quant.py`, `ws.py`.

**OPTIONAL:** `strategy.py` (legacy strategy performance stats, tiny/10 lines, kept only because
`paper.py` still reads the tables), `pipeline.py` (Trading Horizon diagnostics pipeline — useful
during the cutover window, not required afterward).

**RESEARCH:** `backtest.py`, `research.py`, `research_lab.py`, `stress.py`, `ml.py`, `models.py`,
`learning.py`.

**LEGACY:** none standalone at the api/ layer — legacy code is pulled in as a dependency of
`prediction.py`/`paper.py` rather than living in its own dead router.

## 3. Database tables (`backend/app/db/models.py`, ~90 tables)

**CORE:** `trades`, `prediction_features`, `active_drive_decisions`,
`active_drive_decision_consumptions`, `prediction_ledger`, `prediction_resolutions`,
`prediction_cycles`, `market_candles`, `funding_rates`, `open_interest_history`,
`orderbook_snapshots`, `trade_ticks`, `binance_credentials`, `binance_bot_trades`,
`binance_execution_attempts`, `binance_trade_reconciliations`, `binance_protection_capability`,
`exchange_positions`, `trading_control`, `trading_audit_log`, `execution_intent_locks`,
`execution_intent_audit`, `execution_fence_counters`, `live_authorization_leases`,
`live_verification_runs`, `risk_settings`, `risk_settings_audit`, `paper_validation_guard`,
`portfolio`, `indicator_eligibility`, `indicator_eligibility_history`,
`indicator_performance_rollup`, `indicator_governance_settings`, `indicator_notifications`,
`calibration_versions`, `decision_engine_changes`, `signal_candidates`,
`daily_v2_performance`, `daily_indicator_performance`, `daily_quant_signal_performance`,
`user_bot_settings`, `data_download_jobs`, `data_quality_reports`.

**OPTIONAL:** `strategy_performance`, `strategy_rolling_metrics`, `daily_strategy_performance`,
`daily_model_performance`, `sentiment_history`, `liquidation_estimates`, `feature_snapshots`.

**RESEARCH:** `ml_model_registry`, `research_experiments`, `research_lab_experiments`,
`stress_test_runs`, `mlops_models`, `ml_training_jobs`, `ml_model_artifacts`, `ml_notifications`,
`ml_lab_settings`, `mlops_experiments`, `mlops_feature_snapshots`, `mlops_drift_records`,
`mlops_evaluations`, `mlops_retrain_runs`, `advanced_backtest_runs`, `learning_evaluations`.

**LEGACY:** `legacy_neutral_compat_corrections` (name says it all), `trading_horizon_decisions`,
`trading_horizon_timeframe_links`, `trading_horizon_consumptions` (superseded authority, kept
read-only for historical/rollback reference).

## 4. Docker / infrastructure (current `docker-compose.yml`)

| Component | Class | Evidence |
|---|---|---|
| `backend` container | **CORE** | The monolith — everything (decision engine, scheduler, resolver, paper/live execution, REST API) runs in-process as asyncio background tasks started from `main.py` (`start_scheduler()`, `start_data_scheduler()`, `start_decision_resolver()`). There are no separate worker containers today. |
| `redis` container | **CORE** | Used for caching (`data_sources/cache.py` etc.) and rate limiting. |
| `postgres` container | **LEGACY / dead weight** | `DATABASE_URL` (Postgres) is set in `.env.example` and `docker-compose.yml` but **no application code ever opens a Postgres connection** — `db/session.py` reads `PAPER_DATABASE_URL`, which defaults to and is used as SQLite everywhere (`sqlite:////app/data/paper.db`, with WAL mode explicitly tuned for this app's concurrency). No `psycopg2`/`asyncpg` import exists anywhere in `backend/app`. This container can be dropped entirely — it is exactly the kind of "accumulated technical debt" Phase 2 asks to leave behind. |
| host nginx | **CORE** | Terminates HTTP(S), serves the built frontend `dist/`, reverse-proxies `/api/`, `/ws/` to the backend on `127.0.0.1:9000`. |
| `mlops` scheduler (in-process) | **RESEARCH**, always-on today | `start_mlops_scheduler()` runs unconditionally from `main.py` startup — there is currently no feature flag to disable it. Recommended Day-0 addition (additive, ~10 lines, does not touch the money path): gate it behind `settings.enable_mlops_scheduler` (default `false` in V2 `.env`). See `ARCHITECTURE.md`. |

## 5. Frontend pages (`frontend/src/{pages,premium/pages}`, routed in `App.tsx`)

**CORE (the 8 required V2 pages):**
| Phase 2 name | Actual component(s) |
|---|---|
| Dashboard | `DashboardPage.tsx` (+ `PremiumDashboardPage.tsx` if the premium skin is kept) |
| Decision Engine | Not a standalone route — it's `DecisionReasoningCard.tsx` / `ChartDecisionDetails.tsx` on the Dashboard, configured via `DecisionEngineSettings.tsx` on Bot Settings |
| Predictions | `PredictionsPage.tsx`, `PredictionResultsPage.tsx` |
| Paper Trading | `PaperTradingPage.tsx` |
| Binance | `BinanceRealPage.tsx` (+ `BotTradesPage.tsx` for trade history), `PortfolioPage.tsx`, `PositionsPage.tsx` |
| Daily Accuracy | `DailyAccuracyPage.tsx` |
| Bot Settings | `BotSettingsPage.tsx`, `RiskPage.tsx` (risk config folds in here) |
| System Health | `SystemStatusPage.tsx`, `LogsPage.tsx` |

**OPTIONAL:** `MarketPage.tsx`, `ExecutionPage.tsx`, `TradingDiagnosticsPage.tsx`,
`PerformancePage.tsx`, `DailyReportPage.tsx` — useful ops/diagnostic views, not in the required-8.

**RESEARCH:** `BacktestingPage.tsx`, `StressTestPage.tsx`, `ModelCenterPage.tsx`,
`ResearchLabPage.tsx`, `LearningReportPage.tsx`, `SelfLearningPage.tsx`, `ShadowPerformancePage.tsx`.

**LEGACY:** `HyperliquidPage.tsx` (different exchange, no CORE backend router serves it —
`exchanges/bybit.py`/`okx.py` are themselves unreferenced).

## 6. Schedulers / workers (all in-process, see `backend/app/main.py`)

| Job | Class |
|---|---|
| `trading/scheduler.py::start_scheduler()` — paper + Binance execution cycle | **CORE** |
| `data_sources/scheduler.py::start_data_scheduler()` — candle collector | **CORE** |
| `decision_engine/scheduler.py::start_scheduler()` — Active Drive V2 resolver loop | **CORE** |
| `mlops/scheduler.py::start_mlops_scheduler()` — model retraining | **RESEARCH** (recommend feature-flagging off in prod, see above) |

## 7. Other repo areas

- `quantx-trading-horizon/` (separate directory in `$HOME`) is **not separate infrastructure** —
  `git worktree list` confirms it's a linked worktree of this same repo, checked out on
  `feature/trading-horizon-20260715-222314`. It requires no separate audit; Trading Horizon's
  status is already captured above (LEGACY decision authority, 2 CORE-by-dependency utility files).
- `archive/quantx-classic` branch — V1/Classic, already isolated on its own branch. **LEGACY**,
  nothing to do — it's already out of `main`/`perf-and-cleanup-v2-only`.
- `backend/data_copy/`, `frontend/dist.bak-*`, `frontend/dist.old.*`, `backups/*` at repo root —
  point-in-time snapshots from past manual deploys on the *current* VM. **LEGACY artifacts of the
  existing dev box**, not part of the codebase; do not carry these into the V2 image (the V2
  `.dockerignore`/build context excludes them — see `docker-compose.yml`).
