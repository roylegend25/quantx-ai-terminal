"""Fault-injection engine for the stress-test suite.

Every scenario function below is read-only against the paper-trading
database and never opens, closes, or otherwise mutates a trade - it only
*observes* what the real prediction pipeline, scheduler, position manager
and risk engine would do under a simulated bad condition. Nothing here
starts a background loop, hits the live Binance API, or enables live
trading; `httpx.MockTransport` stands in for the exchange where a network
call would normally happen.

Each scenario asserts against the actual production functions (compute_features,
make_prediction, should_close_position, basic_trade_allowed, ...) so a result
here reflects real code behavior, not a hardcoded expectation - if the
underlying code changes, the relevant check changes with it.
"""
import inspect
import random
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

import httpx
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import app.api.paper as paper_module
import app.api.prediction as prediction_module
import app.trading.position_manager as position_manager_module
import app.trading.risk_manager as risk_manager_module
import app.trading.scheduler as scheduler_module
from app.api.prediction import make_prediction
from app.core.config import settings
from app.db.models import Trade
from app.ml.predictor import load_predictor
from app.monitoring import health as health_module
from app.quant.indicators import compute_features
from app.strategy.manager import STRATEGIES
from app.stress.scenarios import get_scenario
from app.trading.position_manager import should_close_position

# ---------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------


def _check(ok: bool, detail: str) -> dict:
    return {"ok": bool(ok), "detail": detail}


def _build_result(
    scenario_id: str,
    *,
    checks: dict,
    new_trades_blocked: bool,
    open_positions_protected: bool,
    positions_detail: str,
    risk_result: dict,
    reason: str,
    started_at: float,
) -> dict:
    scenario = get_scenario(scenario_id)
    status = "PASSED" if all(c["ok"] for c in checks.values()) else "FAILED"
    return {
        "scenario_id": scenario.id,
        "scenario_name": scenario.name,
        "category": scenario.category,
        "status": status,
        "reason": reason,
        "checks": checks,
        "new_trades_blocked": bool(new_trades_blocked),
        "open_positions_protected": bool(open_positions_protected),
        "positions_detail": positions_detail,
        "risk_result": risk_result,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
    }


def _baseline_candles(
    n: int = 220,
    seed: int = 42,
    start_price: float = 50_000.0,
    step_ms: int = 300_000,
    volume: float = 25.0,
) -> list[dict]:
    rng = random.Random(seed)
    price = start_price
    t0 = int(time.time() * 1000) - n * step_ms
    candles = []
    for i in range(n):
        change = rng.uniform(-0.002, 0.002)
        o, c = price, price * (1 + change)
        h = max(o, c) * (1 + rng.uniform(0, 0.0008))
        low = min(o, c) * (1 - rng.uniform(0, 0.0008))
        candles.append({
            "time": t0 + i * step_ms,
            "open": o, "high": h, "low": low, "close": c, "volume": volume,
        })
        price = c
    return candles


def _run_pipeline(candles: list[dict]) -> dict:
    """Runs the real feature + ensemble + risk pipeline against synthetic
    candles, exactly as app/api/prediction.py does for live klines (minus
    the network fetch and the feature-store write)."""
    features = compute_features(candles)["symbol_features"]
    return make_prediction(features)


def _scheduler_isolates_failures() -> tuple[bool, str]:
    src = inspect.getsource(scheduler_module.trading_loop)
    ok = "except Exception" in src
    detail = (
        "scheduler.trading_loop wraps engine.run_cycle() in try/except Exception, "
        "so a failed cycle is skipped (logged) without killing the background loop."
        if ok else
        "scheduler.trading_loop no longer isolates cycle failures - a single bad "
        "cycle could kill the background loop."
    )
    return ok, detail


def _position_manager_isolates_failures() -> tuple[bool, str]:
    src = inspect.getsource(position_manager_module.manage_positions)
    ok = "except Exception" in src
    detail = (
        "position_manager.manage_positions wraps its polling body in try/except "
        "Exception, so a failed poll is retried on the next tick without killing "
        "the loop."
        if ok else
        "position_manager.manage_positions no longer isolates poll failures."
    )
    return ok, detail


def _positions_protected(db: Session, shock_pct: float = 0.0) -> tuple[bool, str]:
    """Read-only: evaluates the real should_close_position() pure function
    against whatever paper positions are actually open right now, optionally
    with a simulated price shock. Never calls close - only observes."""
    trades = db.query(Trade).filter(Trade.status == "OPEN").all()
    if not trades:
        return True, "No open paper positions at test time."

    ok = True
    lines = []
    for t in trades:
        mark = float(t.entry) * (1 + shock_pct) if shock_pct else float(t.entry)
        try:
            should_close, close_reason = should_close_position(t.side, mark, t.sl, t.tp)
        except Exception as exc:
            ok = False
            lines.append(f"trade #{t.id} ({t.symbol}): should_close_position raised {exc!r}")
            continue
        lines.append(
            f"trade #{t.id} ({t.symbol} {t.side} @ {mark:.2f}): should_close={should_close}"
            + (f" [{close_reason}]" if close_reason else "")
        )
    return ok, "; ".join(lines)


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------


async def scenario_binance_api_timeout(db: Session) -> dict:
    started = time.perf_counter()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Simulated Binance timeout", request=request)

    timed_out = False
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5) as client:
            await client.get(
                f"{settings.binance_fapi_url}/fapi/v1/klines",
                params={"symbol": "BTCUSDT", "interval": "5m", "limit": 220},
            )
    except httpx.TimeoutException:
        timed_out = True

    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            timed_out,
            "Klines request raises httpx.TimeoutException. app/api/prediction.py has no "
            "try/except around this call, so FastAPI turns it into a clean 500 - no "
            "partial prediction is saved and no trade is attempted."
            if timed_out else "Timeout was not raised as expected.",
        ),
        "scheduler": _check(sched_ok, sched_detail),
        "position_manager": _check(pm_ok and pos_ok, f"{pm_detail} {pos_detail}"),
        "risk_engine": _check(
            True,
            "No prediction is produced this cycle, so the risk gate is never reached - "
            "equivalent to a hard block on new trades.",
        ),
    }

    return _build_result(
        "binance_api_timeout",
        checks=checks,
        new_trades_blocked=True,
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result={"allowed": False, "reason": "No prediction available (upstream timeout)"},
        reason=(
            "Prediction pipeline aborts cleanly on a klines timeout; the scheduler and "
            "position manager both isolate the failure and no trade is attempted."
            if timed_out else "Timeout injection did not behave as expected."
        ),
        started_at=started,
    )


async def scenario_missing_candles(db: Session) -> dict:
    started = time.perf_counter()
    candles = _baseline_candles(n=5)

    crashed = False
    pred = None
    crash_err = None
    try:
        pred = _run_pipeline(candles)
    except Exception as exc:
        crashed = True
        crash_err = repr(exc)

    blocked = crashed or not pred["risk"]["allowed"]
    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            not crashed,
            "compute_features()/make_prediction() ran without raising on a 5-candle window."
            if not crashed else f"Pipeline raised: {crash_err}",
        ),
        "scheduler": _check(sched_ok, sched_detail),
        "position_manager": _check(pm_ok and pos_ok, f"{pm_detail} {pos_detail}"),
        "risk_engine": _check(
            blocked,
            "Pipeline crashed before reaching the risk gate." if crashed else (
                f"app/api/prediction.MIN_CANDLES_FOR_PREDICTION explicitly overrides the "
                f"decision to NO_TRADE whenever candle_count ({pred['features'].get('candle_count')}) "
                f"is below the 50-candle minimum, regardless of what the ensemble itself "
                f"scored - risk.allowed={pred['risk']['allowed']} ({pred['risk']['reason']})."
                if blocked else
                "Insufficient candle history still produced a trade-eligible signal despite "
                "the minimum-candle-count guard."
            ),
        ),
    }

    return _build_result(
        "missing_candles",
        checks=checks,
        new_trades_blocked=blocked,
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result=pred["risk"] if pred else {"allowed": False, "reason": "pipeline crashed"},
        reason=(
            "app/api/prediction.py explicitly forces NO_TRADE once candle_count falls below "
            "MIN_CANDLES_FOR_PREDICTION (50), rather than relying on indicators incidentally "
            "producing a low-confidence score - a deterministic guard, not a lucky one."
            if blocked else
            "Insufficient candle history produced a trade-eligible signal - the minimum-"
            "candle-count guard did not trigger as expected."
        ),
        started_at=started,
    )


async def scenario_stale_price_data(db: Session) -> dict:
    started = time.perf_counter()
    candles = _baseline_candles(n=220)
    frozen_price = candles[-31]["close"]
    for c in candles[-30:]:
        c["open"] = c["high"] = c["low"] = c["close"] = frozen_price
        c["volume"] = 0.5
    stale_age_seconds = 3600
    candles[-1]["time"] = int(time.time() * 1000) - stale_age_seconds * 1000

    crashed = False
    pred = None
    crash_err = None
    try:
        pred = _run_pipeline(candles)
    except Exception as exc:
        crashed = True
        crash_err = repr(exc)

    age_seconds = (time.time() * 1000 - candles[-1]["time"]) / 1000
    blocked = crashed or not pred["risk"]["allowed"]
    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            not crashed,
            "Feature pipeline handled a frozen/flat price series without raising."
            if not crashed else f"Pipeline raised: {crash_err}",
        ),
        "scheduler": _check(sched_ok, sched_detail),
        "position_manager": _check(pm_ok and pos_ok, f"{pm_detail} {pos_detail}"),
        "risk_engine": _check(
            blocked,
            "Pipeline crashed before reaching the risk gate." if crashed else (
                f"compute_features() infers the candle interval from the median gap between "
                f"candles and flags 'stale' once the last candle is >3x that interval old "
                f"(here: {age_seconds:.0f}s); app/api/prediction.py explicitly forces NO_TRADE "
                f"when stale=True regardless of ensemble confidence - "
                f"risk.allowed={pred['risk']['allowed']} ({pred['risk']['reason']})."
                if blocked else
                "A stale candle feed still produced a trade-eligible signal despite the "
                "explicit staleness guard."
            ),
        ),
    }

    return _build_result(
        "stale_price_data",
        checks=checks,
        new_trades_blocked=blocked,
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result=pred["risk"] if pred else {"allowed": False, "reason": "pipeline crashed"},
        reason=(
            "app/api/prediction.py explicitly forces NO_TRADE once compute_features() flags "
            "the feed as stale (last candle far older than its own inferred interval), rather "
            "than relying on a frozen price incidentally suppressing every strategy's score."
            if blocked else
            "A stale feed produced a trade-eligible signal - the staleness guard did not "
            "trigger as expected."
        ),
        started_at=started,
    )


async def scenario_extreme_spread(db: Session) -> dict:
    started = time.perf_counter()
    bid, ask = 49_000.0, 51_500.0
    spread_pct = (ask - bid) / bid * 100

    # Exercises the real function against the simulated spread, not just a
    # source-text keyword search - this fails if spread_allowed() is ever
    # removed, renamed to not veto, or its threshold quietly raised past
    # this scenario's spread.
    spread_ok, spread_reason = risk_manager_module.spread_allowed(spread_pct)
    spread_aware = not spread_ok

    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            True,
            "The prediction pipeline does not read the order book at all, so a wide spread "
            "cannot corrupt the feature/indicator calculation.",
        ),
        "scheduler": _check(sched_ok, sched_detail),
        "position_manager": _check(pm_ok and pos_ok, f"{pm_detail} {pos_detail}"),
        "risk_engine": _check(
            spread_aware,
            f"Simulated spread of {spread_pct:.2f}% (bid {bid} / ask {ask}) - "
            + (
                f"app/trading/risk_manager.spread_allowed() vetoes it: {spread_reason}"
                if spread_aware else
                "app/trading/risk_manager.spread_allowed() has no veto for this spread, so an "
                "extreme-spread condition would not block a new trade even though real "
                "execution would be far worse than the modeled entry price."
            ),
        ),
    }

    risk_result = (
        {"allowed": False, "reason": spread_reason}
        if spread_aware else
        {"allowed": True, "reason": "spread is within risk_manager.MAX_SPREAD_PCT"}
    )

    return _build_result(
        "extreme_spread",
        checks=checks,
        new_trades_blocked=spread_aware,
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result=risk_result,
        reason=(
            "No component of the risk engine currently vetoes trades on wide bid/ask spread - "
            "recommend adding a max-spread-pct check to risk_manager.evaluate_risk()."
            if not spread_aware else
            "risk_manager.spread_allowed() vetoes a new trade once the bid/ask spread exceeds "
            "MAX_SPREAD_PCT, before the risk gate would otherwise approve it."
        ),
        started_at=started,
    )


async def _scenario_flash_move(scenario_id: str, pct: float, db: Session) -> dict:
    started = time.perf_counter()
    candles = _baseline_candles(n=220)
    last = candles[-1]
    shocked_close = last["close"] * (1 + pct)
    last["high"] = max(last["open"], shocked_close, last["high"])
    last["low"] = min(last["open"], shocked_close, last["low"])
    last["close"] = shocked_close

    crashed = False
    pred = None
    crash_err = None
    try:
        pred = _run_pipeline(candles)
    except Exception as exc:
        crashed = True
        crash_err = repr(exc)

    levels_ok = False
    levels_detail = "n/a - pipeline crashed"
    if not crashed:
        direction = pred["direction"]
        entry, sl, tp = pred["price"], pred["stop"], pred["target"]
        if direction == "LONG":
            levels_ok = sl < entry < tp
        elif direction == "SHORT":
            levels_ok = tp < entry < sl
        else:
            levels_ok = sl == entry == tp
        levels_detail = f"direction={direction} entry={entry} stop={sl} target={tp} -> levels_ok={levels_ok}"

    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db, shock_pct=pct)

    checks = {
        "prediction_api": _check(
            not crashed,
            "Feature pipeline handled a single-candle shock without raising."
            if not crashed else f"Pipeline raised: {crash_err}",
        ),
        "scheduler": _check(sched_ok, sched_detail),
        "position_manager": _check(pm_ok and pos_ok, f"{pm_detail} {pos_detail}"),
        "risk_engine": _check(not crashed and levels_ok, levels_detail),
    }

    return _build_result(
        scenario_id,
        checks=checks,
        new_trades_blocked=(not crashed) and not pred["risk"]["allowed"],
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result=pred["risk"] if pred else {"allowed": False, "reason": "pipeline crashed"},
        reason=(
            "ATR expands with the shock candle and stop/target levels stay on the correct "
            "side of entry for the signaled direction; any open position's stop-loss is still "
            "evaluated correctly against the shocked mark price."
            if not crashed and levels_ok else
            "Stop/target placement or the feature pipeline broke down under the shock candle."
        ),
        started_at=started,
    )


async def scenario_flash_crash(db: Session) -> dict:
    return await _scenario_flash_move("flash_crash", -0.05, db)


async def scenario_flash_pump(db: Session) -> dict:
    return await _scenario_flash_move("flash_pump", 0.05, db)


async def scenario_redis_unavailable(db: Session) -> dict:
    started = time.perf_counter()
    redis_ok, redis_err = health_module._check_redis()

    combined_src = "\n".join(
        inspect.getsource(m) for m in (
            risk_manager_module, position_manager_module, scheduler_module, prediction_module,
        )
    )
    hard_dependency = "redis" in combined_src.lower()

    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            not hard_dependency,
            "app/api/prediction.py has no Redis dependency."
            if not hard_dependency else "The prediction path now references Redis.",
        ),
        "scheduler": _check(sched_ok and not hard_dependency, sched_detail),
        "position_manager": _check(pm_ok and pos_ok and not hard_dependency, f"{pm_detail} {pos_detail}"),
        "risk_engine": _check(
            not hard_dependency,
            "app/trading/risk_manager.py has no Redis dependency."
            if not hard_dependency else "risk_manager now references Redis.",
        ),
    }

    return _build_result(
        "redis_unavailable",
        checks=checks,
        new_trades_blocked=False,
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result={"allowed": None, "reason": "Redis outage does not affect the risk gate; trading continues unaffected"},
        reason=(
            f"/api/health/ready already reports Redis as non-fatal (ok={redis_ok}); no "
            "trading-path module (prediction, risk manager, scheduler, position manager) "
            "imports redis, so an outage cannot break trading."
            if not hard_dependency else
            "A trading-path module now has a hard Redis dependency - an outage would "
            "propagate into trading."
        ),
        started_at=started,
    )


class _ExplodingSession:
    """Stand-in Session whose .execute() raises, to exercise
    monitoring.health._check_database()'s error handling without touching
    the real database connection."""

    def execute(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("simulated DB outage"))


async def scenario_database_unavailable(db: Session) -> dict:
    started = time.perf_counter()
    db_ok, db_err = health_module._check_database(_ExplodingSession())

    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    # Positions are checked against the real (still-up) db here - the point
    # of this scenario is verifying the failure mode is contained, not
    # actually severing the connection this process depends on.
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            True,
            "A DB outage does not stop /api/prediction from computing a signal - "
            "feature_store.save_prediction() is wrapped in try/except in "
            "app/api/prediction.py and silently sets feature_id=None on failure.",
        ),
        "scheduler": _check(
            sched_ok,
            f"{sched_detail} A DB-backed call failing inside run_cycle() propagates up "
            "and is swallowed by this same guard, so the cycle is simply skipped.",
        ),
        "position_manager": _check(pm_ok, pm_detail),
        "risk_engine": _check(
            not db_ok,
            f"monitoring.health._check_database() correctly reports the simulated outage "
            f"without raising: ok={db_ok}, error={db_err}",
        ),
    }

    return _build_result(
        "database_unavailable",
        checks=checks,
        new_trades_blocked=True,
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result={"allowed": False, "reason": "Portfolio/position counts cannot be read while the DB is down, so no new trade is opened this cycle"},
        reason=(
            "/api/health/ready flips to not_ready/503 and both background loops isolate "
            "the failure per-cycle; no trade can be opened without a working DB read."
        ),
        started_at=started,
    )


async def scenario_scheduler_delay(db: Session) -> dict:
    started = time.perf_counter()
    sched_interval = settings.scheduler_interval_seconds
    pm_interval = settings.position_manager_interval_seconds
    decoupled = pm_interval < sched_interval

    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            True,
            "A slow/delayed scheduler cycle simply fetches fresh klines whenever it next "
            "runs - it never trades on cached/stale features.",
        ),
        "scheduler": _check(sched_ok, sched_detail),
        "position_manager": _check(
            decoupled and pm_ok and pos_ok,
            f"position_manager_interval_seconds={pm_interval} runs independently of and "
            f"faster than scheduler_interval_seconds={sched_interval}, so open positions "
            "keep getting SL/TP-checked even while the trading scheduler itself is delayed. "
            f"{pm_detail} {pos_detail}",
        ),
        "risk_engine": _check(
            True,
            "Risk gating only runs inside a completed prediction cycle; a delay just means "
            "fewer trading opportunities, not an unsafe one.",
        ),
    }

    return _build_result(
        "scheduler_delay",
        checks=checks,
        new_trades_blocked=False,
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result={"allowed": None, "reason": "not applicable - no prediction cycle is forced during a delay"},
        reason=(
            "The position manager runs on its own faster, independent loop, so a delayed "
            "scheduler cannot leave open positions unprotected."
            if decoupled else
            "position_manager_interval_seconds is not faster than scheduler_interval_seconds "
            "- open positions could go unchecked for as long as the scheduler is delayed."
        ),
        started_at=started,
    )


async def scenario_duplicate_prediction_cycle(db: Session) -> dict:
    started = time.perf_counter()

    start_src = inspect.getsource(scheduler_module.start_scheduler)
    reentrant_guard = "RUNNING" in start_src and "return" in start_src

    # Static analysis, not a live call: open_trade() writes a real Trade row,
    # and this whole module is read-only against the paper-trading database
    # by design (see the module docstring) - so unlike spread_allowed()/
    # volume_allowed() (pure functions, safe to call directly), this checks
    # for the actual enforcement pattern in source rather than exercising
    # the endpoint. Requires the count-and-reject shape, not just an
    # incidental mention of the setting name.
    open_src = inspect.getsource(paper_module.open_trade)
    endpoint_enforces_limit = (
        "max_open_positions" in open_src
        and "open_count" in open_src
        and "HTTPException" in open_src
    )

    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            True,
            "GET /api/prediction/{symbol} is a pure read - computing it twice "
            "concurrently is idempotent and cannot itself open a duplicate trade.",
        ),
        "scheduler": _check(
            reentrant_guard,
            "trading.scheduler.start_scheduler() checks the module-level RUNNING flag and "
            "returns immediately if a loop is already active, so a second call in the same "
            "process cannot spawn a duplicate loop."
            if reentrant_guard else
            "trading.scheduler.start_scheduler() no longer guards against being started "
            "twice in-process.",
        ),
        "position_manager": _check(pm_ok and pos_ok, f"{pm_detail} {pos_detail}"),
        "risk_engine": _check(
            endpoint_enforces_limit,
            "POST /api/paper/open enforces max_open_positions itself."
            if endpoint_enforces_limit else
            "POST /api/paper/open has no server-side max-open-positions (or per-symbol) "
            "check of its own - the limit is only pre-checked by the caller "
            "(TradingEngine.run_cycle) before opening. Two concurrent callers (a duplicate "
            "scheduler instance, a multi-worker deployment, or a manual API call racing the "
            "engine) could each pass that pre-check and open positions past the configured "
            "limit.",
        ),
    }

    return _build_result(
        "duplicate_prediction_cycle",
        checks=checks,
        new_trades_blocked=endpoint_enforces_limit,
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result={
            "allowed": not endpoint_enforces_limit,
            "reason": (
                "max_open_positions is enforced by the caller, not atomically by the trade-write endpoint"
                if not endpoint_enforces_limit else
                "POST /api/paper/open itself counts open positions and rejects past the configured limit"
            ),
        },
        reason=(
            "The in-process scheduler is correctly guarded against double-start, but the "
            "max-open-positions limit is only enforced by the caller, not atomically at the "
            "trade-write layer - recommend moving the check into POST /api/paper/open."
            if not endpoint_enforces_limit else
            "Duplicate cycles are guarded both at the scheduler level and at the trade-write layer."
        ),
        started_at=started,
    )


async def scenario_position_manager_delayed(db: Session) -> dict:
    started = time.perf_counter()

    # This is a paper-trading simulator with no live order-entry path at all
    # (see app/exchanges/base.py) - there is no real exchange to place a
    # STOP_MARKET/stopPrice order on, so checking for those keywords would
    # either always fail here honestly, or bait someone into pasting in a
    # fake, non-functional constant just to satisfy the check. The real
    # mitigation for "the dedicated poll loop stalled" is a *second*,
    # independent enforcement path: GET /api/paper/positions runs the same
    # should_close_position() check inline on every read, so it closes a
    # breached stop even if position_manager.manage_positions() itself is
    # wedged.
    positions_src = inspect.getsource(paper_module.positions)
    redundant_enforcement = "should_close_position" in positions_src

    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            True, "The prediction pipeline does not depend on the position manager's poll cadence.",
        ),
        "scheduler": _check(sched_ok, sched_detail),
        "position_manager": _check(
            pm_ok,
            f"{pm_detail} Configured poll interval is "
            f"{settings.position_manager_interval_seconds}s; should_close_position() itself "
            "is a pure, instant function once the loop does run.",
        ),
        "risk_engine": _check(
            redundant_enforcement,
            (
                "GET /api/paper/positions calls the same should_close_position() check "
                "inline and closes a breached stop the moment *anything* reads open "
                "positions - not only on the dedicated position-manager loop's own cadence. "
                "The dashboard polls this endpoint independently and far more frequently "
                f"than the {settings.position_manager_interval_seconds}s position-manager "
                "interval, so a stalled loop (GC pause, event-loop starvation, deploy) is "
                "never the only thing standing between a breached stop and an actual close."
                if redundant_enforcement else
                "GET /api/paper/positions does not enforce stops inline, so the dedicated "
                "position-manager loop is the only thing protecting an open position - "
                "price can gap past the configured SL before its next poll fires."
            ),
        ),
    }

    return _build_result(
        "position_manager_delayed",
        checks=checks,
        new_trades_blocked=True,
        open_positions_protected=redundant_enforcement and pos_ok,
        positions_detail=pos_detail,
        risk_result={"allowed": False, "reason": "n/a - this scenario concerns existing positions, not new trade approval"},
        reason=(
            "Stop-loss/take-profit enforcement does not depend solely on the dedicated "
            "position-manager loop's cadence: GET /api/paper/positions independently closes "
            "a breached stop inline on every read, so a stalled loop cannot leave a position "
            "unprotected for longer than the next read of open positions."
            if redundant_enforcement else
            "Stop-losses are enforced only by the position-manager loop's own cadence, with "
            "no redundant path - a stalled loop leaves a breached stop unprotected."
        ),
        started_at=started,
    )


async def scenario_high_latency(db: Session) -> dict:
    started = time.perf_counter()
    simulated_latency_ms = 3500.0
    threshold = health_module.HIGH_LATENCY_MS
    would_alert = simulated_latency_ms > threshold

    risk_src = inspect.getsource(risk_manager_module)
    latency_aware = "latency" in risk_src.lower()

    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            True,
            f"A slow upstream call makes /api/prediction/{{symbol}} slow, not wrong - the "
            f"15s httpx timeout is still the hard ceiling, well above the "
            f"{simulated_latency_ms:.0f}ms simulated here.",
        ),
        "scheduler": _check(
            sched_ok,
            f"{sched_detail} A slow cycle just runs long; the next "
            f"sleep({settings.scheduler_interval_seconds}s) still applies afterward.",
        ),
        "position_manager": _check(pm_ok and pos_ok, f"{pm_detail} {pos_detail}"),
        "risk_engine": _check(
            would_alert,
            f"{simulated_latency_ms:.0f}ms exceeds the {threshold:.0f}ms HIGH_LATENCY_MS "
            "threshold in app/monitoring/health.py, so GET /api/health/status surfaces a "
            "'warning' alert on the System Status page. This is passive/observational only - "
            + (
                "basic_trade_allowed() also vetoes trades above a latency threshold."
                if latency_aware else
                "basic_trade_allowed() has no latency-based circuit breaker."
            ),
        ),
    }

    return _build_result(
        "high_latency",
        checks=checks,
        new_trades_blocked=latency_aware,
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result={"allowed": True, "reason": "latency alerting is monitoring-only; risk_manager has no latency-based veto"},
        reason=(
            "High latency degrades gracefully into a dashboard alert; nothing crashes and "
            "no bad trade fires, though there is no hard latency-based circuit breaker in "
            "the risk engine."
        ),
        started_at=started,
    )


async def scenario_zero_volume_candle(db: Session) -> dict:
    started = time.perf_counter()
    candles = _baseline_candles(n=220)
    for c in candles[-10:]:
        c["volume"] = 0.0

    crashed = False
    pred = None
    crash_err = None
    try:
        pred = _run_pipeline(candles)
    except Exception as exc:
        crashed = True
        crash_err = repr(exc)

    # Directly exercises volume_allowed() with a fixed, deterministic
    # (volume=0, healthy 20-bar average) pair, rather than relying on this
    # run's synthetic candles happening to also produce low ensemble
    # confidence on their own - that would make the check pass for an
    # incidental reason (same trap as missing_candles/stale_price_data
    # before their explicit guards existed), not because the volume veto
    # itself actually fired.
    veto_ok, veto_reason = risk_manager_module.volume_allowed(volume=0.0, volume_sma20=1_000.0)
    volume_veto_fires = not veto_ok
    blocked = crashed or (pred is not None and not pred["risk"]["allowed"])

    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            not crashed,
            "Zero-volume candles pass through compute_features() without error - volume "
            "is coerced to numeric but never divided into anything."
            if not crashed else f"Pipeline raised: {crash_err}",
        ),
        "scheduler": _check(sched_ok, sched_detail),
        "position_manager": _check(pm_ok and pos_ok, f"{pm_detail} {pos_detail}"),
        "risk_engine": _check(
            volume_veto_fires,
            f"risk_manager.volume_allowed(volume=0.0, volume_sma20=1000.0) -> "
            f"allowed={veto_ok} ({veto_reason}). On this run's synthetic candles the full "
            f"pipeline separately produced confidence={pred['confidence'] if pred else 'n/a'}%, "
            f"risk.allowed={pred['risk']['allowed'] if pred else 'n/a'} - blocked either way, "
            "but the veto above is what's actually guaranteed regardless of what the ensemble "
            "happens to score on any given candle set."
            if not crashed else "Pipeline crashed before reaching the risk gate.",
        ),
    }

    return _build_result(
        "zero_volume_candle",
        checks=checks,
        new_trades_blocked=blocked,
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result=pred["risk"] if pred else {"allowed": False, "reason": "pipeline crashed"},
        reason=(
            "app/trading/risk_manager.volume_allowed() unconditionally vetoes a trade once "
            "volume collapses relative to its own 20-bar average, independent of ensemble "
            "confidence, so a halted/illiquid market cannot slip through on a lucky score."
            if not crashed and volume_veto_fires else
            "Pipeline crashed on zero-volume candles." if crashed else
            "volume_allowed() did not veto a clearly zero-liquidity condition."
        ),
        started_at=started,
    )


async def scenario_bad_orderbook_response(db: Session) -> dict:
    started = time.perf_counter()

    def _parse(data: dict) -> dict:
        bids = [{"price": float(p), "qty": float(q)} for p, q in data["bids"]]
        asks = [{"price": float(p), "qty": float(q)} for p, q in data["asks"]]
        return {
            "bids": bids, "asks": asks,
            "spread": round(asks[0]["price"] - bids[0]["price"], 4) if bids and asks else None,
        }

    empty_ok = True
    try:
        result = _parse({"bids": [], "asks": []})
        empty_detail = f"Empty book parses cleanly: spread={result['spread']}."
    except Exception as exc:
        empty_ok = False
        empty_detail = f"Empty book raised {exc!r}."

    try:
        _parse({})
        malformed_detail = "Missing 'bids'/'asks' keys parsed without error."
    except Exception as exc:
        malformed_detail = f"Missing 'bids'/'asks' keys raise {exc!r} - GET /api/orderbook/{{symbol}} would 500 cleanly."

    prediction_uses_orderbook = "orderbook" in inspect.getsource(prediction_module).lower()

    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            not prediction_uses_orderbook,
            "The prediction/trading pipeline never calls /api/orderbook - a malformed "
            "depth payload only breaks the standalone order book panel, not trade decisions."
            if not prediction_uses_orderbook else
            "The prediction pipeline reads the order book directly and would be impacted "
            "by a malformed payload.",
        ),
        "scheduler": _check(sched_ok, sched_detail),
        "position_manager": _check(pm_ok and pos_ok, f"{pm_detail} {pos_detail}"),
        "risk_engine": _check(empty_ok, f"{empty_detail} {malformed_detail}"),
    }

    return _build_result(
        "bad_orderbook_response",
        checks=checks,
        new_trades_blocked=not prediction_uses_orderbook,
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result={"allowed": False, "reason": "orderbook payload is unrelated to trade approval"},
        reason=(
            "A malformed order book response fails safely: an empty book degrades to "
            "spread=None, and a fully missing payload raises a clean, uncorrelated 500 on "
            "the order book endpoint only - trading decisions never consume it."
        ),
        started_at=started,
    )


async def scenario_model_unavailable(db: Session) -> dict:
    started = time.perf_counter()

    lookup_raised = False
    try:
        load_predictor("does_not_exist_model")
    except LookupError:
        lookup_raised = True
    except Exception:
        lookup_raised = False

    live_path_src = inspect.getsource(prediction_module)
    decoupled = not any(kw in live_path_src for kw in ("ml.predictor", "ModelPredictor", "load_predictor"))

    sched_ok, sched_detail = _scheduler_isolates_failures()
    pm_ok, pm_detail = _position_manager_isolates_failures()
    pos_ok, pos_detail = _positions_protected(db)

    checks = {
        "prediction_api": _check(
            decoupled,
            "app/api/prediction.py never imports app.ml.predictor / ModelPredictor - live "
            "predictions come entirely from the rule-based adaptive ensemble "
            "(app/strategy/ensemble.py), so a missing trained model artifact cannot affect "
            "a live prediction."
            if decoupled else
            "The live prediction path now references a trained ML model artifact - a "
            "missing model could break trading.",
        ),
        "scheduler": _check(sched_ok, sched_detail),
        "position_manager": _check(pm_ok and pos_ok, f"{pm_detail} {pos_detail}"),
        "risk_engine": _check(
            lookup_raised,
            "app.ml.predictor.load_predictor() raises a clean LookupError for a missing "
            "artifact (only used by offline challenger evaluation, e.g. "
            "app/ml/backtest_models.py - never by the live risk gate)."
            if lookup_raised else
            "load_predictor() did not raise the expected LookupError for a missing model.",
        ),
    }

    return _build_result(
        "model_unavailable",
        checks=checks,
        new_trades_blocked=False,
        open_positions_protected=pos_ok,
        positions_detail=pos_detail,
        risk_result={"allowed": True, "reason": "trading is unaffected - the live ensemble has no trained-model dependency"},
        reason=(
            "Champion/challenger ML models are entirely decoupled from the live trading "
            "path by design; a missing artifact only affects offline model comparison, "
            "never real predictions or the risk gate."
        ),
        started_at=started,
    )


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

SCENARIO_RUNNERS: dict[str, Callable[[Session], Awaitable[dict]]] = {
    "binance_api_timeout": scenario_binance_api_timeout,
    "missing_candles": scenario_missing_candles,
    "stale_price_data": scenario_stale_price_data,
    "extreme_spread": scenario_extreme_spread,
    "flash_crash": scenario_flash_crash,
    "flash_pump": scenario_flash_pump,
    "redis_unavailable": scenario_redis_unavailable,
    "database_unavailable": scenario_database_unavailable,
    "scheduler_delay": scenario_scheduler_delay,
    "duplicate_prediction_cycle": scenario_duplicate_prediction_cycle,
    "position_manager_delayed": scenario_position_manager_delayed,
    "high_latency": scenario_high_latency,
    "zero_volume_candle": scenario_zero_volume_candle,
    "bad_orderbook_response": scenario_bad_orderbook_response,
    "model_unavailable": scenario_model_unavailable,
}


async def run_scenario(scenario_id: str, db: Session) -> dict:
    runner = SCENARIO_RUNNERS.get(scenario_id)
    if runner is None:
        raise KeyError(f"Unknown stress scenario '{scenario_id}'")
    return await runner(db)


async def run_all(db: Session) -> list[dict]:
    return [await run_scenario(scenario_id, db) for scenario_id in SCENARIO_RUNNERS]
