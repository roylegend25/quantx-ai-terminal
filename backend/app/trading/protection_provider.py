"""Protection Provider selection and unified TP/SL placement (Phase 26
Algo Order Provider).

Two ways to place a protective (TP/SL) order on Binance USDS-M Futures:
  - "classic": POST /fapi/v1/order (STOP_MARKET / TAKE_PROFIT_MARKET) - the
    original order-entry endpoint (app.exchanges.binance_futures_client).
  - "algo": POST /fapi/v1/algoOrder (algoType=CONDITIONAL) - Binance's Algo
    Order API (app.exchanges.binance_algo_provider), required by accounts
    where the classic endpoint rejects STOP_MARKET/TAKE_PROFIT_MARKET with
    code -4120 STOP_ORDER_SWITCH_ALGO (see app.trading.diagnostics for the
    investigation; per Binance's own docs, USDS-M Futures migrated
    conditional orders to this service as of 2025-12-09).

This module is the ONLY place that decides which one to use for a given
mode, and the ONLY place that persists the answer
(app.trading.protection_capability) so every order after the first -4120
skips straight to the working path. app.trading.execution_router and the
/protect repair endpoint both call through here instead of talking to
either underlying provider directly - the UI and the rest of the codebase
never need to know which one is active for this account.

Failure policy for one leg (TP or SL): retry once immediately, then retry
again with exponential backoff, then give up and report a clear failure -
never silently drop a leg. A -4120 on the classic provider is NOT treated
as a transient failure to retry: it is immediate, unambiguous evidence
this account needs the algo provider, so it triggers migration on the
spot and the SAME call is retried once via the algo provider before
giving up.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.exchanges.binance_algo_provider import BinanceAlgoProvider
from app.exchanges.binance_errors import BinanceError
from app.exchanges.binance_futures_client import BinanceFuturesClient
from app.trading import protection_capability as capability

CLASSIC = capability.CLASSIC
ALGO = capability.ALGO

_ALGO_SWITCH_CODE = -4120
MAX_RETRIES = 2  # total attempts per leg = 1 + MAX_RETRIES
_BACKOFF_BASE_SECONDS = 0.5


def _is_algo_switch_error(e: Exception) -> bool:
    return isinstance(e, BinanceError) and e.code == _ALGO_SWITCH_CODE


def _safe_error(e: Exception) -> str:
    from app.trading.margin import RiskValidationError
    if isinstance(e, (BinanceError, RiskValidationError)):
        return getattr(e, "message", str(e))
    return f"{type(e).__name__}: {e}"


@dataclass
class LegResult:
    ok: bool
    order_id: int | None = None
    provider: str | None = None
    error: str | None = None
    attempts: int = 0
    migrated: bool = False
    raw: dict | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "order_id": self.order_id, "provider": self.provider,
            "error": self.error, "attempts": self.attempts, "migrated": self.migrated, "raw": self.raw,
        }


@dataclass
class ProtectionResult:
    provider_used: str
    tp: LegResult
    sl: LegResult
    latency_ms: float
    migrated: bool = False

    @property
    def ok(self) -> bool:
        return self.tp.ok and self.sl.ok

    def to_dict(self) -> dict:
        return {
            "provider_used": self.provider_used, "tp": self.tp.to_dict(), "sl": self.sl.to_dict(),
            "latency_ms": self.latency_ms, "migrated": self.migrated,
        }


async def _attempt_once(*, kind: str, provider: str, client: BinanceFuturesClient, symbol: str,
                        position_side: str, price: float, quantity: float | None, hedge_mode: bool):
    """Places exactly one TP or SL order via the given provider. Raises on
    any failure (including -4120) - callers decide what a raised exception
    means (retry, migrate, or give up)."""
    if provider == ALGO:
        algo = BinanceAlgoProvider(client)
        place = algo.place_take_profit if kind == "tp" else algo.place_stop_loss
        order = await place(symbol, position_side, price, quantity=quantity, hedge_mode=hedge_mode)
        return order.algo_id, order.raw
    place = client.place_take_profit if kind == "tp" else client.place_stop_loss
    order = await place(symbol, position_side, price, quantity=quantity, hedge_mode=hedge_mode)
    return order.order_id, order.to_dict()


async def _place_with_retry(*, kind: str, provider: str, client: BinanceFuturesClient, symbol: str,
                            position_side: str, price: float, quantity: float | None,
                            hedge_mode: bool) -> LegResult:
    """Retry + exponential backoff for transient failures. A -4120 is
    re-raised immediately (untouched by the retry loop) so the caller can
    migrate providers instead of wasting retries on a structural rejection."""
    attempt = 0
    last_error: Exception | None = None
    while attempt <= MAX_RETRIES:
        attempt += 1
        try:
            order_id, raw = await _attempt_once(
                kind=kind, provider=provider, client=client, symbol=symbol, position_side=position_side,
                price=price, quantity=quantity, hedge_mode=hedge_mode,
            )
            return LegResult(ok=True, order_id=order_id, provider=provider, attempts=attempt, raw=raw)
        except Exception as e:
            if _is_algo_switch_error(e):
                raise
            last_error = e
            if attempt <= MAX_RETRIES:
                await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    return LegResult(ok=False, provider=provider, error=_safe_error(last_error), attempts=attempt)


async def place_leg(*, client: BinanceFuturesClient, mode: str, symbol: str, position_side: str, kind: str,
                    price: float | None, quantity: float | None, hedge_mode: bool = False) -> LegResult:
    """Places one TP or SL leg using whichever provider this mode is known
    (or assumed) to need, auto-migrating and persisting on the first -4120.
    Never raises."""
    if price is None:
        return LegResult(ok=False, error=f"No {kind} price computed for this entry")

    provider = capability.get_provider(mode) or CLASSIC
    try:
        result = await _place_with_retry(
            kind=kind, provider=provider, client=client, symbol=symbol, position_side=position_side,
            price=price, quantity=quantity, hedge_mode=hedge_mode,
        )
    except Exception as e:
        if _is_algo_switch_error(e) and provider == CLASSIC:
            reason = f"{kind.upper()} leg: classic endpoint returned -4120 STOP_ORDER_SWITCH_ALGO"
            capability.set_provider(mode, ALGO, reason=reason)
            result = await _place_with_retry(
                kind=kind, provider=ALGO, client=client, symbol=symbol, position_side=position_side,
                price=price, quantity=quantity, hedge_mode=hedge_mode,
            )
            result.migrated = True
            return result
        error = _safe_error(e)
        capability.record_error(mode, error)
        return LegResult(ok=False, provider=provider, error=error)

    if result.ok and provider == CLASSIC and capability.get_provider(mode) is None:
        capability.set_provider(mode, CLASSIC, reason="Classic protective order accepted by Binance")
    if not result.ok and result.error:
        capability.record_error(mode, result.error)
    return result


async def place_protection(*, client: BinanceFuturesClient, mode: str, symbol: str, position_side: str,
                           tp: float | None, sl: float | None, quantity: float | None,
                           hedge_mode: bool = False) -> ProtectionResult:
    """Places TP and SL for a fresh entry (or a full repair), each leg
    independently retried/migrated via place_leg."""
    start = time.perf_counter()
    tp_result = await place_leg(client=client, mode=mode, symbol=symbol, position_side=position_side,
                                kind="tp", price=tp, quantity=quantity, hedge_mode=hedge_mode)
    sl_result = await place_leg(client=client, mode=mode, symbol=symbol, position_side=position_side,
                                kind="sl", price=sl, quantity=quantity, hedge_mode=hedge_mode)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    provider_used = sl_result.provider or tp_result.provider or capability.get_provider(mode) or CLASSIC
    migrated = tp_result.migrated or sl_result.migrated
    return ProtectionResult(provider_used=provider_used, tp=tp_result, sl=sl_result,
                            latency_ms=latency_ms, migrated=migrated)


async def cancel_leg(
    client: BinanceFuturesClient, symbol: str, order_id: int | None, provider: str,
    client_algo_id: str | None = None, client_order_id: str | None = None,
) -> None:
    """Cancels one tracked TP or SL order through the correct surface.
    `order_id` is an algoId when provider is ALGO, a classic orderId
    otherwise - either may be omitted in favor of its client_* twin (Debug
    1.pdf section 5), but never both for the same surface."""
    if provider == ALGO:
        await BinanceAlgoProvider(client).cancel(algo_id=order_id, client_algo_id=client_algo_id)
        # Phase 29: resolve_protection's algo lookup cache (app.trading.
        # protection) can otherwise report this exact id as still active
        # for up to BINANCE_ALGO_TTL_SECONDS after it was just canceled.
        from app.trading import protection
        protection.reset_algo_lookup_cache()
    else:
        await client.cancel_order(symbol, order_id=order_id, orig_client_order_id=client_order_id)
