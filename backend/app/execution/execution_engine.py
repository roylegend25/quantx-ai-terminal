"""Institutional-style execution engine for QuantX paper trading.

Sits between a trade decision (app.engine.trading_engine) and the paper
ledger (app.api.paper). Order routing, slippage and partial fills are all
simulated against real read-only market data (order-book depth, live
price via app.exchanges), but the only state ever mutated is the existing
paper Trade/Portfolio tables, via the same POST /api/paper/open endpoint
the scheduler already called before this module existed.

There is no code path here - or anywhere it calls into - that can reach a
real exchange's order-entry endpoint. app.exchanges adapters are read-only
by construction, and this module never places, amends or cancels a real
order; "filling" an order means simulating what a live venue would have
done to this process's own paper ledger.
"""
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.core.security import create_internal_service_token
from app.execution import execution_metrics as metrics
from app.execution.order_router import (
    OrderRejected,
    OrderType,
    derive_price,
    route_order,
    validate_order,
)
from app.execution.retry import RetryConfig, with_retry
from app.execution.slippage import actual_slippage_bps, execution_quality
from app.monitoring.logging import get_logger, log_event

logger = get_logger("quantx.execution")

BINANCE_FAPI = "https://fapi.binance.com"
PAPER_API = "http://127.0.0.1:8000"

MAX_SIGNAL_AGE_SECONDS = 30.0
DUPLICATE_WINDOW_SECONDS = 15.0
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
CIRCUIT_BREAKER_COOLDOWN_SECONDS = 60.0


@dataclass
class ExecutionResult:
    status: str  # FILLED | PARTIAL | REJECTED | CANCELLED | ERROR
    order_id: str
    symbol: str
    side: str
    order_type: str
    requested_qty: float
    filled_qty: float
    avg_fill_price: float | None
    expected_slippage_bps: float | None
    actual_slippage_bps: float | None
    execution_quality: str | None
    latency_ms: float
    retries: int
    reason: str | None
    trade_id: int | None = None


class CircuitBreaker:
    """Trips after N consecutive execution failures and rejects new orders
    for a cooldown window - a single flaky retry doesn't trip it, but a
    genuine outage stops the engine from hammering a broken dependency."""

    def __init__(self, threshold: int, cooldown_seconds: float):
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._tripped_at: float | None = None

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._tripped_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.threshold:
            self._tripped_at = time.time()

    @property
    def is_open(self) -> bool:
        """True = tripped, currently rejecting new orders."""
        if self._tripped_at is None:
            return False
        if time.time() - self._tripped_at >= self.cooldown_seconds:
            # Cooldown elapsed - allow a trial order through.
            self._tripped_at = None
            self._consecutive_failures = 0
            return False
        return True

    def status(self) -> dict:
        return {
            "open": self.is_open,
            "consecutive_failures": self._consecutive_failures,
            "threshold": self.threshold,
            "cooldown_seconds": self.cooldown_seconds,
        }


class ExecutionEngine:
    def __init__(self):
        self._breaker = CircuitBreaker(CIRCUIT_BREAKER_FAILURE_THRESHOLD, CIRCUIT_BREAKER_COOLDOWN_SECONDS)
        self._recent_signatures: dict[str, float] = {}
        self._token: str | None = None

    def _token_header(self) -> dict:
        if self._token is None:
            self._token = create_internal_service_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _is_duplicate(self, signature: str) -> bool:
        now = time.time()
        expired = [k for k, t in self._recent_signatures.items() if now - t > DUPLICATE_WINDOW_SECONDS]
        for k in expired:
            del self._recent_signatures[k]
        if signature in self._recent_signatures:
            return True
        self._recent_signatures[signature] = now
        return False

    def status(self) -> dict:
        return {
            "engine": "operational",
            "mode": "paper",
            "circuit_breaker": self._breaker.status(),
            "safety": {
                "max_signal_age_seconds": MAX_SIGNAL_AGE_SECONDS,
                "duplicate_window_seconds": DUPLICATE_WINDOW_SECONDS,
                "max_open_positions": settings.max_open_positions,
                "max_risk_per_trade_pct": settings.max_risk_per_trade_pct,
            },
            "tracked_signatures": len(self._recent_signatures),
        }

    async def submit_order(
        self,
        symbol: str,
        side: str,
        usdt_size: float,
        order_type: OrderType | str = OrderType.MARKET,
        price: float | None = None,
        sl: float | None = None,
        tp: float | None = None,
        feature_id: int | None = None,
        regime: str | None = None,
        strategies: dict | None = None,
        signal_time: datetime | None = None,
        open_positions: int = 0,
        equity: float | None = None,
        exchange: str = "binance",
    ) -> ExecutionResult:
        start = time.perf_counter()
        order_id = uuid.uuid4().hex[:12]
        order_type = OrderType(order_type) if isinstance(order_type, str) else order_type
        symbol = symbol.upper()
        side = side.upper()

        def reject(reason: str, status: str = "REJECTED") -> ExecutionResult:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            result = ExecutionResult(
                status=status, order_id=order_id, symbol=symbol, side=side,
                order_type=order_type.value, requested_qty=0.0, filled_qty=0.0,
                avg_fill_price=None, expected_slippage_bps=None, actual_slippage_bps=None,
                execution_quality=None, latency_ms=latency_ms, retries=0, reason=reason,
            )
            metrics.record(result)
            log_event(logger, message="order_rejected", event="execution", symbol=symbol, error=reason, latency_ms=latency_ms)
            return result

        # --- safety gates (cheap, no network) --------------------------------
        if self._breaker.is_open:
            return reject("circuit breaker open - too many recent execution failures")

        if signal_time is not None:
            age = (datetime.now(timezone.utc) - signal_time).total_seconds()
            if age > MAX_SIGNAL_AGE_SECONDS:
                return reject(f"stale signal: {age:.1f}s old (max {MAX_SIGNAL_AGE_SECONDS}s)")

        signature = f"{symbol}:{side}:{round(usdt_size, 2)}:{feature_id}"
        if self._is_duplicate(signature):
            return reject("duplicate order rejected (same signal submitted within dedupe window)")

        if open_positions >= settings.max_open_positions:
            return reject("max open positions reached - risk limit")

        # --- fetch reference price, validate, and route ----------------------
        try:
            async def _route():
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(f"{BINANCE_FAPI}/fapi/v1/ticker/price", params={"symbol": symbol})
                    r.raise_for_status()
                    market_price = float(r.json()["price"])

                qty = usdt_size / market_price

                if sl is not None and equity:
                    risk_amount = abs(market_price - sl) * qty
                    max_risk_amount = equity * (settings.max_risk_per_trade_pct / 100)
                    if risk_amount > max_risk_amount:
                        raise OrderRejected(
                            f"order risk ${risk_amount:.2f} exceeds configured max "
                            f"{settings.max_risk_per_trade_pct}% of equity (${max_risk_amount:.2f})"
                        )

                order_price = price
                if order_price is None and order_type != OrderType.MARKET:
                    order_price = derive_price(order_type, side, market_price)

                validated = validate_order(symbol, side, order_type, qty, order_price)
                routed = await route_order(validated, market_price, exchange=exchange)
                return validated, routed

            (validated, routed), retry_outcome = await with_retry(_route, RetryConfig())
        except OrderRejected as e:
            return reject(str(e))
        except Exception as e:
            self._breaker.record_failure()
            return reject(f"execution failed after retries: {e!r}", status="ERROR")

        if routed.status in ("REJECTED", "CANCELLED"):
            # A legitimate exchange-side outcome (FOK couldn't fully fill,
            # post-only would have crossed, ...) - not a system failure.
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            result = ExecutionResult(
                status=routed.status, order_id=order_id, symbol=symbol, side=side,
                order_type=order_type.value, requested_qty=validated.qty, filled_qty=0.0,
                avg_fill_price=None, expected_slippage_bps=routed.expected_slippage_bps,
                actual_slippage_bps=None, execution_quality=None, latency_ms=latency_ms,
                retries=retry_outcome.retries, reason=routed.reason,
            )
            metrics.record(result)
            return result

        # --- book the simulated fill against the paper ledger -----------------
        filled_notional = routed.filled_qty * routed.avg_fill_price
        try:
            async def _open():
                # httpx renders a None value as an empty query-string value
                # (e.g. "sl="), which FastAPI then rejects for `float | None`
                # params - omit anything unset instead of sending it empty.
                params = {
                    "symbol": symbol, "side": side, "usdt_size": filled_notional,
                    "entry_price": routed.avg_fill_price,
                }
                if sl is not None:
                    params["sl"] = sl
                if tp is not None:
                    params["tp"] = tp
                if feature_id is not None:
                    params["feature_id"] = feature_id

                async with httpx.AsyncClient(timeout=15, headers=self._token_header()) as client:
                    r = await client.post(
                        f"{PAPER_API}/api/paper/open",
                        params=params,
                        json={"regime": regime, "strategies": strategies},
                    )
                    r.raise_for_status()
                    return r.json()

            open_resp, open_retry = await with_retry(_open, RetryConfig())
        except Exception as e:
            self._breaker.record_failure()
            return reject(f"paper ledger booking failed: {e!r}", status="ERROR")

        self._breaker.record_success()

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        actual_bps = actual_slippage_bps(routed.reference_price, routed.avg_fill_price, side)
        fill_ratio = routed.filled_qty / validated.qty if validated.qty else 0.0
        quality = execution_quality(abs(actual_bps), latency_ms, fill_ratio)

        result = ExecutionResult(
            status=routed.status,  # FILLED | PARTIAL
            order_id=order_id, symbol=symbol, side=side, order_type=order_type.value,
            requested_qty=validated.qty, filled_qty=routed.filled_qty,
            avg_fill_price=routed.avg_fill_price, expected_slippage_bps=routed.expected_slippage_bps,
            actual_slippage_bps=actual_bps, execution_quality=quality, latency_ms=latency_ms,
            retries=retry_outcome.retries + open_retry.retries,
            reason=routed.reason, trade_id=(open_resp.get("trade") or {}).get("id"),
        )
        metrics.record(result)
        log_event(
            logger, message="order_executed", event="execution", symbol=symbol,
            latency_ms=latency_ms, error=None,
        )
        return result


engine = ExecutionEngine()
