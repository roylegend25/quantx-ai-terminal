"""Protection watchdog (Phase 27, section 9).

Every WATCHDOG_INTERVAL_SECONDS: pull live Binance positions and open
orders, find any position missing TP/SL, and record the finding so the
dashboard can show a warning banner without waiting for the next order
attempt to surface it. A no-op in PAPER/locked mode - it never constructs a
client outside a real mode.

Auto-protect (BINANCE_AUTO_PROTECT_POSITIONS) is opt-in and, when enabled,
computes a conservative bracket from the position's own liquidation price
rather than any strategy-specific target - the watchdog has no signal
context, only "this position is naked, put *something* safe on it".
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.db.models import ExchangePositionRow
from app.db.session import SessionLocal
from app.monitoring.logging import get_logger, log_event
from app.trading import modes, protection, protection_provider

# Phase 29: the interval is now configurable (BINANCE_WATCHDOG_INTERVAL_SECONDS,
# default 12s, up from a hardcoded 8s) and each scan reuses the shared
# snapshot cache (app.exchanges.binance_snapshot_service) instead of its own
# get_positions/get_open_orders calls, so a scan that lands inside another
# caller's still-fresh cache window costs zero extra Binance requests.
WATCHDOG_INTERVAL_SECONDS = settings.binance_watchdog_interval_seconds
RUNNING = False
logger = get_logger("quantx.protection_watchdog")

# Last scan's findings - read by the API layer so the dashboard doesn't
# need its own signed Binance round trip just to show the warning banner.
# repair_failed / critical (Phase 26 Algo Order Provider): positions where
# auto-repair itself failed - a strictly worse state than "unprotected but
# a repair attempt hasn't run yet", surfaced separately so the UI can show
# it as more urgent.
LAST_SCAN: dict = {
    "checked_at": None, "unprotected": [], "repair_failed": [], "critical": False,
    "available": True, "reason": None,
}


def _tracked_algo_ids(mode: str, symbol: str) -> tuple[int | None, int | None]:
    db = SessionLocal()
    try:
        row = db.query(ExchangePositionRow).filter_by(mode=mode, symbol=symbol).first()
        return (row.tp_algo_id, row.sl_algo_id) if row else (None, None)
    finally:
        db.close()


def _default_bracket(position) -> tuple[float, float]:
    """A conservative auto-protect bracket anchored on the position's own
    liquidation price - halfway between mark and liquidation, so it can
    never itself trigger past liquidation. This is a last-resort safety
    net, not a trading decision; the watchdog has no strategy context."""
    mark = position.mark_price
    liq = position.liquidation_price
    if position.side == "LONG":
        sl = mark - (mark - liq) / 2 if liq and liq < mark else mark * 0.98
        tp = mark * 1.02
    else:
        sl = mark + (liq - mark) / 2 if liq and liq > mark else mark * 1.02
        tp = mark * 0.98
    return round(tp, 2), round(sl, 2)


async def _scan_once() -> dict:
    global LAST_SCAN
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    if modes.effective_mode() not in modes.REAL_MODES or not modes.binance_configured():
        LAST_SCAN = {"checked_at": now, "unprotected": [], "repair_failed": [], "critical": False,
                    "available": True, "reason": None}
        return LAST_SCAN

    from app.api.portfolio import get_account_snapshot, get_read_client
    from app.exchanges.binance_snapshot_service import snapshot_service
    from app.trading.execution_router import router as execution_router

    client = get_read_client()
    mode = modes.MODE_LIVE  # get_read_client() is always the production account
    try:
        # Phase 29: reuses the same shared cache as every portfolio/risk
        # endpoint - if a browser poll already refreshed positions/orders
        # inside the TTL window, this scan makes zero Binance calls of its
        # own, and a rate-limited refresh still returns the last known good
        # data here (never an empty list mistaken for "no positions").
        _, positions, _ = await get_account_snapshot(client)
        open_orders = await snapshot_service.get_open_orders(client)
    except Exception as e:
        LAST_SCAN = {"checked_at": now, "unprotected": [], "repair_failed": [], "critical": False,
                    "available": False, "reason": repr(e)}
        return LAST_SCAN

    unprotected = []
    repair_failed = []
    for pos in positions:
        tp_algo_id, sl_algo_id = _tracked_algo_ids(mode, pos.symbol)
        # Phase 26 Algo Order Provider: algo orders never appear in
        # get_open_orders, so any tracked algo id is verified individually.
        verified = await protection.resolve_protection(
            client, pos.symbol, open_orders=open_orders, tp_algo_id=tp_algo_id, sl_algo_id=sl_algo_id,
        )
        if protection.is_unprotected(verified.status):
            unprotected.append({
                "symbol": pos.symbol, "side": pos.side, "quantity": pos.quantity,
                "entry_price": pos.entry_price, "mark_price": pos.mark_price,
                "liquidation_price": pos.liquidation_price, "protection_status": verified.status,
            })

    if unprotected:
        log_event(
            logger, message="unprotected_position_detected", level=logging.WARNING, category="trading",
            symbols=[p["symbol"] for p in unprotected],
        )
        if settings.binance_auto_protect_positions:
            live_client = execution_router.provider().client
            for pos in positions:
                if pos.symbol not in {p["symbol"] for p in unprotected}:
                    continue
                tp, sl = _default_bracket(pos)
                try:
                    hedge_mode = await live_client.get_position_mode()
                except Exception:
                    hedge_mode = False

                # Repair through the unified provider (auto-migrates/retries
                # exactly like a fresh entry's protection - see
                # app.trading.protection_provider) instead of the classic
                # client directly, so a -4120 account is repaired correctly
                # on the very first watchdog pass too.
                result = await protection_provider.place_protection(
                    client=live_client, mode=mode, symbol=pos.symbol, position_side=pos.side,
                    tp=tp, sl=sl, quantity=pos.quantity, hedge_mode=hedge_mode,
                )
                if result.provider_used == protection_provider.ALGO:
                    from app.trading.execution_router import _store_protection_metadata
                    _store_protection_metadata(mode, pos.symbol, protection_provider.ALGO,
                                               None, None, result.tp.order_id, result.sl.order_id)
                else:
                    from app.trading.execution_router import _store_protection_metadata
                    _store_protection_metadata(mode, pos.symbol, protection_provider.CLASSIC,
                                               result.tp.order_id, result.sl.order_id, None, None)

                if result.ok:
                    modes.audit("watchdog_auto_protected", symbol=pos.symbol,
                                detail={"tp": tp, "sl": sl, "provider": result.provider_used})
                else:
                    reason = result.tp.error or result.sl.error or "Repair attempt failed"
                    log_event(logger, message="watchdog_auto_protect_failed", level=logging.CRITICAL,
                             category="trading", symbol=pos.symbol, error=reason)
                    modes.audit("watchdog_repair_failed", symbol=pos.symbol, detail={"reason": reason})
                    repair_failed.append({"symbol": pos.symbol, "side": pos.side, "reason": reason})

                    if settings.binance_close_if_protection_fails:
                        close_result = await execution_router.close_position(symbol=pos.symbol)
                        modes.audit("position_closed_due_to_unprotected", symbol=pos.symbol,
                                    detail={"reason": reason, "close_ok": close_result.ok, "source": "watchdog"})

    LAST_SCAN = {
        "checked_at": now, "unprotected": unprotected, "repair_failed": repair_failed,
        "critical": bool(repair_failed), "available": True, "reason": None,
    }
    return LAST_SCAN


async def watchdog_loop():
    global RUNNING
    while RUNNING:
        try:
            if settings.binance_protection_watchdog_enabled:
                await _scan_once()
        except Exception as e:
            log_event(logger, message="watchdog_error", level=logging.ERROR, category="trading", error=repr(e))
        await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)


def start_protection_watchdog():
    global RUNNING
    if RUNNING:
        return
    RUNNING = True
    asyncio.create_task(watchdog_loop())


def stop_protection_watchdog():
    global RUNNING
    RUNNING = False
