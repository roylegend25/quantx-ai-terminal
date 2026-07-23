# Decision/execution call graph

Replaces `TRADING_HORIZON_CALL_GRAPH.md`, which documented Trading Horizon's
separate authority-issuance subsystem. Trading Horizon has been removed from
the production decision/execution path entirely (see
`app/trading_horizon/authority.py`/`service.py` docstrings - both are
deprecated, historical-only, and proven unreachable from production by
`backend/tests/test_horizon_not_invoked_in_production.py`). Every safety
behavior it used to provide now lives directly on the persisted
`ActiveDriveDecision` itself, via `app/decision_engine/execution_gate.py`.

## Single-authoritative-decision flow

```text
market data (Binance klines, cache fallback)
  -> app/quant/indicators.compute_features()
  -> app/decision_engine/v2.ActiveDriveV2Engine.evaluate()   # calibrated confidence, trade levels, net edge
  -> app/decision_engine/ledger.persist()                    # writes one ActiveDriveDecision row
  -> app/decision_engine/execution_gate.finalize_decision_for_execution()
       - stamps valid_from/valid_until (freshness window)
       - checks confidence/point-margin/trade-level/edge gates already
         evaluated by v2.py
       - runs the portfolio risk gate (app/trading/risk_manager.py)
       - sets execution_approved + final_block_reason
  -> paper/live safety gate (app/trading/modes.py)
  -> app/trading/execution_router.ExecutionRouter.open_position(decision_id=...)
       -> app/decision_engine/execution_gate.validate_decision_for_consumption()
            - ownership/symbol/direction match
            - not expired (valid_until)
            - execution_approved is True
            - exactly-once: ActiveDriveDecisionConsumption primary-key insert
       -> PaperExecutionProvider or BinanceExecutionProvider submits
  -> order -> position -> performance update
```

There is exactly one persisted decision per (symbol, execution timeframe,
cycle) driving execution - `app/api/prediction.py`'s in-flight request
coalescing (per `(user, engine, symbol, interval)` key) ensures concurrent
callers share one compute+persist instead of racing independent ones.

## Entry-capable surfaces

- **Scheduler** (`app/engine/trading_engine.py::_run_symbol_cycle`) resolves a
  static profile timeframe (`app/decision_engine/profiles.py`), calls the
  same in-process prediction pipeline as `GET /api/prediction`, then calls
  `execution_router.open_position(decision_id=...)` on the freshly-persisted,
  execution-approved decision.
- **`POST /api/paper/open`** (`app/api/paper.py`) is a separate, manual,
  authenticated-caller path for opening a paper position without going
  through the automated decision/execution pipeline - it does not require
  `execution_gate.py`/an `ActiveDriveDecision` at all. It is used for
  manual/QA paper trades, not the automated scheduler flow.
- Binance provider order placement is private to
  `BinanceExecutionProvider.open_position`, gated by
  `settings.binance_live_enabled` (always `false` in Premium X Dark today).
- Research/backtest simulation functions have no exchange/provider access.

## Protective and exposure-reducing paths

`close_position`, emergency stop, cancel/cancel-all, TP/SL edits, protection
repair/watchdog, and position/order synchronization do not require a fresh
authoritative decision at all - they act on already-open positions/orders and
remain available even if decision evaluation is degraded or unavailable.

## What no longer exists

- `GET /api/timeframes/{symbol}/horizon` returns HTTP 410
  (`TRADING_HORIZON_REMOVED`) - see `app/api/timeframes.py`.
- `resolve_authoritative_timeframe` (`app/trading_horizon/current_authority.py`)
  no longer depends on Horizon at all - it resolves a static profile
  timeframe directly.
- Active Drive V1 (the legacy ensemble-based engine) has been removed from
  Premium X Dark entirely - see the standalone QuantX Classic repository.
