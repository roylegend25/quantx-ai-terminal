# Trading Horizon execution call graph

## Side-effect-free preview path

```text
Dashboard / public client
  -> GET /api/timeframes/{symbol}/horizon
  -> calculate multi-timeframe preview
  -> preview_blocked | preview_informational | preview_ready
  -> persisted=false, horizon_decision_id=null
```

The GET route never writes `trading_horizon_decisions`, timeframe links,
consumptions, execution intents, or outbox records. `preview_ready` is display
state only and is not executable authority.

## Authoritative new-entry path

```text
TradingScheduler
  -> TradingEngine.run_cycle / _run_symbol_cycle
  -> in-process evaluate_and_issue_horizon_authority (no HTTP preview/issuance round trip)
     -> create one evaluation context and bounded deadline
     -> same-process scheduler calls with the same cycle idempotency key share
        one in-flight/completed evaluation result
     -> evaluate each unique required execution/confirmation/bias timeframe once,
        with bounded concurrency and per-timeframe timeouts
     -> enforce readiness, edge, confidence, unanimity, profile, risk revision,
        durable-link, freshness, context identity and non-degraded gates
     -> fingerprint immutable evidence and atomically commit exactly one
        trading_horizon_decisions row, execution snapshot and timeframe links
  -> return opaque horizon_decision_id (no second prediction request)
  -> ExecutionRouter.open_position(horizon_decision_id, user_id, symbol, risk-budget request)
     -> reload and validate immutable authority, its execution link and exact V2 row
     -> load the issuance-time execution snapshot (direction, confidence, stop, target, holding and revisions)
     -> acquire Redis + database fenced execution lease (database-only safe fallback on outage)
     -> start owner-token heartbeat
     -> current account/market checks may reduce size or block, but cannot replace snapshot evidence
     -> PaperExecutionProvider or BinanceExecutionProvider performs reversible validation
     -> immediately before submission: recheck owner/fence/profile/authority and atomically consume authority
     -> exactly one provider submission
     -> persist terminal audit, stop heartbeat, owner-checked release
```

No complete caller-created `HorizonDecision` or prediction thesis is accepted. The scheduler passes
only the opaque persisted ID plus symbol/account and a bounded risk request. Confirmation, bias, context, chart, weekly and
monthly timeframes are evidence; the persisted profile's one execution
timeframe is the only accepted `timeframe` at the router boundary.

## Audited entry-capable surfaces

- Scheduler, bot start/resume and background cycles share `TradingEngine` and
  the common router path above.
- There are no separate retry or recovery entry workers. A restarted worker
  must reacquire the durable fenced lease; consumed decisions and terminal
  idempotency records reject replay.
- `POST /api/paper/open` is now an internal-service-only ledger sink downstream
  of `PaperExecutionProvider`; authenticated external/manual callers receive
  `HORIZON_AUTHORITY_REQUIRED` and cannot treat it as an alternative entry API.
- Binance provider `place_market_order` entry use is private to
  `BinanceExecutionProvider.open_position` after the pre-submit guard.
- Active Drive V1 is neither selectable as an authoritative linked decision
  nor valid in a persisted Horizon authority record.
- Research/backtest `_open_position` functions are deterministic simulations
  and have no exchange/provider access.

## Protective and exposure-reducing paths

`close_position`, emergency stop, cancel/cancel-all, TP/SL edits, protection
repair/watchdog, and position/order synchronization deliberately do not require
new-entry authority or the entry lease. They cannot create positive exposure
and remain available when Horizon evaluation or idempotency infrastructure is
unavailable.
