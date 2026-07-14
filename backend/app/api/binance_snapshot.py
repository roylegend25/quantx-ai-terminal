"""One shared Binance snapshot endpoint (Phase 29).

Root cause of "Rate limited by Binance": the frontend used to poll six-plus
separate /api/portfolio/binance/* and /api/trading/binance/* endpoints per
page, each of which (before this phase) made its own independent signed
Binance calls. Every one of those endpoints now reads from the same
process-wide cache (app.exchanges.binance_snapshot_service), so this
endpoint doesn't need to make any Binance calls of its own beyond what
those endpoints already share - it just assembles their already-cached
answers into one response so the frontend can move from N poll loops to
one (see frontend/src/hooks/useBinanceSnapshot.ts).

Never returns keys, secrets or signatures - every field here is already
safe-by-construction from the modules it's assembled from.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.exchanges.binance_rate_limiter import limiter
from app.exchanges.binance_snapshot_service import snapshot_service
from app.trading import modes
from app.trading.protection_watchdog import LAST_SCAN

router = APIRouter(prefix="/api/binance", tags=["binance-snapshot"])


async def _build_snapshot(db: Session, force_refresh: bool) -> dict:
    from app.api.portfolio import (
        binance_balances,
        binance_income,
        binance_orders,
        binance_positions,
        binance_summary,
        get_read_client,
    )
    from app.api.trading_control import live_readiness

    if force_refresh and modes.binance_configured():
        # A manual sync still goes through the rate limiter and the
        # single-flight lock exactly like a background poll - it just skips
        # the TTL check, it never bypasses a cooldown (section 15: "never
        # retry-loop", the limiter still refuses a withheld priority).
        client = get_read_client()
        try:
            await snapshot_service.get_account_bundle(client, force_refresh=True)
            await snapshot_service.get_open_orders(client, force_refresh=True)
        except Exception:
            pass  # the per-section reads below still report the resulting state safely

    summary = await binance_summary(db=db)
    balances = await binance_balances()
    positions = await binance_positions(db=db)
    orders = await binance_orders()
    income = await binance_income()
    readiness = await live_readiness(db=db)

    errors = [
        e["reason"] for e in (summary, balances, positions, orders, income)
        if e.get("available") is False and e.get("reason")
    ]

    protection_rows = [
        {"symbol": p["symbol"], "status": p.get("protection_status"), "provider": p.get("protection_provider")}
        for p in positions.get("positions", [])
    ]
    unprotected = [p for p in protection_rows if p["status"] and p["status"] != "PROTECTED"]

    rate_status = limiter.status()
    stale = bool(summary.get("stale") or positions.get("stale") or orders.get("stale") or rate_status["rate_limited"])
    generated_at = datetime.now(timezone.utc).isoformat()

    # Debug 3.pdf section 12: each position is self-describing (source/
    # stale/updated_at) so a consumer - the new Dashboard Open Positions
    # card in particular - never has to guess whose data this is or how
    # fresh it is from the array alone. Never a new Binance call: reuses
    # the exact stale/timestamp facts already computed above for the
    # whole snapshot.
    real_positions = [
        {**p, "source": "binance_real", "stale": stale, "updated_at": generated_at}
        for p in positions.get("positions", [])
    ]

    return {
        "updated_at": generated_at,
        "stale": stale,
        "rate_limited": rate_status["rate_limited"],
        "retry_after_seconds": rate_status["retry_after_seconds"],
        "account": summary if summary.get("available") else None,
        # True once Binance has ever confirmed the real open-positions list
        # (even if stale/cached) - False only when it's genuinely never
        # been read (not configured, or every read has failed). Lets the
        # UI say "no real positions confirmed by Binance" only when that's
        # actually true, instead of whenever the list is merely empty.
        "positions_available": positions.get("available", False),
        "balances": balances.get("balances", []),
        "positions": real_positions,
        "orders": orders.get("orders", []),
        "income": income.get("income", []),
        "protection": {
            "unprotected": unprotected,
            "watchdog_last_checked_at": LAST_SCAN.get("checked_at"),
            "watchdog_critical": LAST_SCAN.get("critical", False),
            "watchdog_repair_failed": LAST_SCAN.get("repair_failed", []),
        },
        "readiness": readiness,
        "errors": errors,
    }


@router.get("/snapshot")
async def get_snapshot(db: Session = Depends(get_db)):
    """Read-only: serves the shared TTL cache, never forces a Binance call.
    This is the endpoint every page should poll instead of assembling its
    own view from several separate calls (frontend/src/hooks/
    useBinanceSnapshot.ts)."""
    return await _build_snapshot(db, force_refresh=False)


@router.post("/snapshot/refresh")
async def refresh_snapshot(db: Session = Depends(get_db)):
    """Manual sync (section 15): forces a fresh read when the rate limiter
    allows it. When rate-limited, it still returns the (possibly stale)
    cached snapshot with `rate_limited`/`retry_after_seconds` set rather
    than raising or retry-looping - the caller decides whether to try
    again after the countdown."""
    return await _build_snapshot(db, force_refresh=True)


@router.get("/rate-limit-status")
async def rate_limit_status():
    """Observability (section 16): current governor state - requests/min,
    weight used, last 429/418, cooldown/ban windows, top endpoints by call
    count. No Binance credentials or raw payloads, ever."""
    return limiter.status()
