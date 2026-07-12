"""Two strictly separate portfolio surfaces (Phase 23).

/api/portfolio/paper/*   - the simulated ledger, always available, never
                           influenced by the active trading mode.
/api/portfolio/binance/* - the REAL Binance Futures account, read through a
                           read-only production client (order methods are
                           structurally disabled on it), viewable even while
                           live trading is locked. Binance is the source of
                           truth; nothing here is merged with paper data.

Binance endpoints never crash the page: failures return 200 with
{"available": false, "reason": <safe classification>} - and no response
anywhere in this module can contain keys, secrets or signatures.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import app.api.paper as paper_api
from app.core.config import settings
from app.db.models import BinanceBotTrade, ExchangePositionRow, TradingAuditLog
from app.db.session import get_db
from app.exchanges.binance_errors import (
    BinanceError,
    BinanceNetworkError,
    BinanceNotConfigured,
    BinancePermissionError,
    BinanceRateLimitError,
    BinanceTimestampError,
)
from app.exchanges.binance_futures_client import BinanceFuturesClient
from app.exchanges.binance_snapshot_service import snapshot_service
from app.risk import settings_repository
from app.trading import modes, protection

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

# One long-lived READ-ONLY production client for account views. Its
# _post/_delete raise unconditionally, so it can never place or cancel an
# order regardless of any lock state.
_read_client: BinanceFuturesClient | None = None


def get_read_client() -> BinanceFuturesClient:
    global _read_client
    if _read_client is None:
        _read_client = BinanceFuturesClient(testnet=False, read_only=True)
    return _read_client


# Phase 29: (account, positions, daily_pnl) together back the Live Margin
# Calculator (2s poll), the Execution Pipeline/Risk Management endpoints (8s
# poll) and the protection watchdog - all of them need the same three
# numbers. This used to be its own ad hoc 1.5s cache; it now delegates to
# app.exchanges.binance_snapshot_service, the ONE shared, rate-limiter-aware
# cache every Binance-reading endpoint in the app goes through, so
# overlapping polls from multiple open cards (and multiple browser tabs)
# cost one signed Binance round trip, not one each - and a rate-limited
# refresh returns the last known good snapshot instead of failing outright.
async def get_account_snapshot(client: BinanceFuturesClient, force_refresh: bool = False) -> tuple:
    return await snapshot_service.get_account_bundle(client, force_refresh=force_refresh)


def _safe_unavailable_reason(e: Exception) -> str:
    """Classify a Binance failure into a UI-safe sentence - never the raw
    exception (which could reference request internals)."""
    if isinstance(e, BinanceNotConfigured):
        return "Binance API key not configured"
    if isinstance(e, BinancePermissionError):
        return "API key rejected - check futures permission and IP whitelist"
    if isinstance(e, BinanceRateLimitError):
        return "Rate limited by Binance - retry shortly"
    if isinstance(e, BinanceTimestampError):
        return "Server clock drift while signing - retry"
    if isinstance(e, BinanceNetworkError):
        return "Binance unreachable"
    if isinstance(e, BinanceError):
        return "Binance rejected the request"
    return "Binance account unavailable"


def _binance_unavailable(e: Exception) -> dict:
    """Safe failure shape for a Binance-reading endpoint. When the failure
    is a rate limit (real or the local rate limiter withholding the call -
    see app.exchanges.binance_rate_limiter), `retry_after_seconds` lets the
    UI show a countdown instead of a bare "try again" - never guessed, only
    ever a value Binance itself (or a just-computed local cooldown) reported."""
    rate_limited = isinstance(e, BinanceRateLimitError)
    return {
        "available": False,
        "reason": _safe_unavailable_reason(e),
        "rate_limited": rate_limited,
        "retry_after_seconds": getattr(e, "retry_after_seconds", None) if rate_limited else None,
    }


def _risk_summary(db: Session) -> dict:
    risk = settings_repository.get_settings(db=db)
    control = modes.get_control(db)
    mode = modes.effective_mode(db)
    return {
        "active_mode": mode,
        "max_daily_loss_pct": risk["max_daily_loss_pct"],
        "max_daily_loss_usdt": settings.binance_max_daily_loss_usdt,
        "max_open_positions": risk["max_open_positions"],
        "min_confidence_to_trade": risk["min_confidence_to_trade"],
        "max_leverage": settings.binance_max_leverage,
        "max_notional_per_trade": settings.binance_max_notional_per_trade,
        "allowed_symbols": settings.binance_allowed_symbols,
        "binance_live_enabled_by_server": settings.binance_live_enabled,
        "binance_live_unlocked_by_user": control["live_unlocked"],
        "kill_switch_active": control["kill_switch_active"],
        "kill_switch_reason": control["kill_switch_reason"],
    }


# ============================================================= paper space

@router.get("/paper/summary")
async def paper_summary(db: Session = Depends(get_db)):
    paper = await paper_api.portfolio(db=db)
    return {
        "mode": "PAPER",
        "available": True,
        "balance": paper["balance"],
        "equity": paper["equity"],
        "available_margin": paper["available_margin"],
        "margin_used": paper["total_margin_used"],
        "unrealized_pnl": paper["unrealized_pnl"],
        "realized_pnl": paper["total_pnl"],
        "daily_pnl": paper["daily_pnl"],
        "win_rate": paper["win_rate"],
        "wins": paper["wins"],
        "losses": paper["losses"],
        "open_positions": paper["open_positions"],
        "max_open_positions": paper["max_open_positions"],
        "total_notional_exposure": paper["total_notional_exposure"],
        "nearest_liquidation_distance_pct": paper["nearest_liquidation_distance_pct"],
        "risk": _risk_summary(db),
    }


@router.get("/paper/positions")
async def paper_positions(db: Session = Depends(get_db)):
    data = await paper_api.positions(db=db)
    return {"mode": "PAPER", "positions": data["positions"]}


@router.get("/paper/orders")
async def paper_orders():
    # the paper engine fills immediately - there are never resting orders
    return {"mode": "PAPER", "orders": []}


@router.get("/paper/trades")
async def paper_trades(db: Session = Depends(get_db)):
    data = await paper_api.history(db=db)
    return {"mode": "PAPER", "trades": data["trades"]}


# =========================================================== binance space

def _staleness_envelope(*sections: dict) -> dict:
    """Folds one or more app.exchanges.binance_snapshot_service section-meta
    dicts into the fields the UI needs to show cached data honestly instead
    of wiping it (Phase 29, section 13): `stale` if ANY contributing section
    served a cached value after its own refresh failed, the soonest
    `retry_after_seconds` across them, and how old the oldest contributing
    section is."""
    stale = any(s["stale"] for s in sections)
    retry_after = min((s["retry_after_seconds"] for s in sections if s["retry_after_seconds"] is not None), default=None)
    ages = [s["age_seconds"] for s in sections if s["age_seconds"] is not None]
    return {
        "stale": stale,
        "retry_after_seconds": retry_after,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "data_age_seconds": max(ages) if ages else None,
    }


@router.get("/binance/summary")
async def binance_summary(db: Session = Depends(get_db)):
    if not modes.binance_configured():
        return {"mode": "BINANCE_LIVE", **_binance_unavailable(BinanceNotConfigured("no keys"))}
    client = get_read_client()
    try:
        account, positions, daily_pnl = await get_account_snapshot(client)
    except Exception as e:
        return {"mode": "BINANCE_LIVE", **_binance_unavailable(e)}

    total_notional = sum(p.notional for p in positions)
    nearest_liq = None
    for p in positions:
        if p.liquidation_price and p.mark_price:
            dist = abs(p.mark_price - p.liquidation_price) / p.mark_price * 100
            nearest_liq = dist if nearest_liq is None else min(nearest_liq, dist)

    return {
        "mode": "BINANCE_LIVE",
        "available": True,
        "total_wallet_balance": round(account.total_wallet_balance, 2),
        "available_balance": round(account.available_balance, 2),
        "margin_balance": round(account.total_margin_balance, 2),
        "unrealized_pnl": round(account.total_unrealized_pnl, 2),
        "daily_pnl": round(daily_pnl, 2),
        "margin_used": round(account.total_initial_margin, 2),
        "maintenance_margin": round(account.total_maintenance_margin, 2),
        "free_margin": round(account.available_balance, 2),
        "open_positions": len(positions),
        "total_notional_exposure": round(total_notional, 2),
        "nearest_liquidation_distance_pct": round(nearest_liq, 2) if nearest_liq is not None else None,
        "risk": _risk_summary(db),
        **_staleness_envelope(snapshot_service.section_meta()["account"]),
    }


@router.get("/binance/balances")
async def binance_balances():
    if not modes.binance_configured():
        return {"mode": "BINANCE_LIVE", **_binance_unavailable(BinanceNotConfigured("no keys")), "balances": []}
    try:
        rows = await snapshot_service.get_balances(get_read_client())
    except Exception as e:
        return {"mode": "BINANCE_LIVE", **_binance_unavailable(e), "balances": []}
    interesting = [b for b in rows if b.balance or b.available or b.asset in ("USDT", "BTC", "ETH")]
    return {
        "mode": "BINANCE_LIVE",
        "available": True,
        "balances": [
            {
                "asset": b.asset,
                "available": round(b.available, 8),
                "locked": round(b.locked, 8),
                "total": round(b.balance, 8),
            }
            for b in interesting
        ],
        **_staleness_envelope(snapshot_service.section_meta()["balances"]),
    }


@router.get("/binance/positions")
async def binance_positions(db: Session = Depends(get_db)):
    if not modes.binance_configured():
        return {"mode": "BINANCE_LIVE", **_binance_unavailable(BinanceNotConfigured("no keys")), "positions": []}
    client = get_read_client()
    try:
        # Phase 29: shared with binance_summary/the watchdog/the margin
        # calculator - a rate-limited refresh transparently serves the last
        # known good positions/orders here instead of raising, so this
        # branch is now reached only when NO good data has ever been read.
        _, live, _ = await snapshot_service.get_account_bundle(client)
        open_orders = await snapshot_service.get_open_orders(client)
    except Exception as e:
        return {"mode": "BINANCE_LIVE", **_binance_unavailable(e), "positions": []}

    # local mirror row ids (written by the sync loop when live trading is
    # active) let the UI target PATCH /api/trading/binance/positions/{id}/risk
    local_ids = {
        row.symbol: row.id
        for row in db.query(ExchangePositionRow)
        .filter(ExchangePositionRow.mode == modes.MODE_LIVE)
        .all()
    }

    # Phase 26 Algo Order Provider: which surface (classic/algo) each
    # position's TP/SL is on, and any tracked algo ids - written by
    # BinanceExecutionProvider whenever protection is placed or replaced.
    provider_rows = {
        row.symbol: row
        for row in db.query(ExchangePositionRow).filter(ExchangePositionRow.mode == modes.MODE_LIVE).all()
    }

    payload = []
    for p in live:
        # Phase 27: TP/SL is ALWAYS derived fresh from Binance's own open
        # orders here (app.trading.protection) - never from local DB state,
        # and never assumed present just because a same-type order exists
        # (a plain non-reduce-only order doesn't count as protection). Algo
        # orders never appear in get_open_orders, so any tracked algo id is
        # looked up individually (TTL-cached, see app.trading.protection)
        # and merged in (resolve_protection).
        row = provider_rows.get(p.symbol)
        verified = await protection.resolve_protection(
            client, p.symbol, open_orders=open_orders,
            tp_algo_id=row.tp_algo_id if row else None, sl_algo_id=row.sl_algo_id if row else None,
        )
        payload.append({
            **p.to_dict(),
            "id": local_ids.get(p.symbol),
            "sl": verified.sl_price,
            "tp": verified.tp_price,
            "sl_order_id": verified.sl_order_id,
            "tp_order_id": verified.tp_order_id,
            "protection_status": verified.status,
            "protection_provider": row.protection_provider if row else None,
        })
    meta = snapshot_service.section_meta()
    return {
        "mode": "BINANCE_LIVE", "available": True, "positions": payload,
        **_staleness_envelope(meta["account"], meta["orders"]),
    }


@router.get("/binance/orders")
async def binance_orders():
    if not modes.binance_configured():
        return {"mode": "BINANCE_LIVE", **_binance_unavailable(BinanceNotConfigured("no keys")), "orders": []}
    try:
        open_orders = await snapshot_service.get_open_orders(get_read_client())
    except Exception as e:
        return {"mode": "BINANCE_LIVE", **_binance_unavailable(e), "orders": []}
    return {
        "mode": "BINANCE_LIVE", "available": True, "orders": [o.to_dict() for o in open_orders],
        **_staleness_envelope(snapshot_service.section_meta()["orders"]),
    }


@router.get("/binance/trades")
async def binance_trades(db: Session = Depends(get_db)):
    """Real account trade history, labelled: BOT_TRADE when the order id
    matches this system's own journal, SYNCED_FROM_BINANCE otherwise (which
    covers manual exchange trades - we cannot prove authorship of orders we
    didn't place, so they are labelled by provenance, not guessed). LOW
    priority historical data (Phase 29) - shared/cached across every caller
    rather than re-queried per symbol on every poll."""
    if not modes.binance_configured():
        return {"mode": "BINANCE_LIVE", **_binance_unavailable(BinanceNotConfigured("no keys")), "trades": []}
    client = get_read_client()
    bot_order_ids = {
        row.order_id for row in db.query(BinanceBotTrade.order_id).all()
    }
    try:
        trades = await snapshot_service.get_trades(client, settings.binance_allowed_symbols)
    except Exception as e:
        return {"mode": "BINANCE_LIVE", **_binance_unavailable(e), "trades": []}
    result = []
    for t in trades:
        d = t.to_dict()
        d["label"] = "BOT_TRADE" if t.order_id in bot_order_ids else "SYNCED_FROM_BINANCE"
        result.append(d)
    result.sort(key=lambda t: t.get("time") or 0, reverse=True)
    return {
        "mode": "BINANCE_LIVE", "available": True, "trades": result[:100],
        **_staleness_envelope(snapshot_service.section_meta()["trades"]),
    }


@router.get("/binance/income")
async def binance_income(limit: int = 50):
    """Recent income rows: realized PnL, commission, funding fees."""
    if not modes.binance_configured():
        return {"mode": "BINANCE_LIVE", **_binance_unavailable(BinanceNotConfigured("no keys")), "income": []}
    try:
        rows = await snapshot_service.get_income(get_read_client(), limit=min(max(limit, 1), 200))
    except Exception as e:
        return {"mode": "BINANCE_LIVE", **_binance_unavailable(e), "income": []}
    return {
        "mode": "BINANCE_LIVE", "available": True, "income": rows,
        **_staleness_envelope(snapshot_service.section_meta()["income"]),
    }


# ================================================================== audit

@router.get("/audit")
async def audit_log(limit: int = 100, db: Session = Depends(get_db)):
    """Persisted real-trading audit trail (mode changes, orders, kill
    switch, TP/SL edits). Complements the in-memory /api/logs stream."""
    limit = max(1, min(limit, 500))
    rows = db.query(TradingAuditLog).order_by(TradingAuditLog.id.desc()).limit(limit).all()
    return {
        "events": [
            {
                "id": r.id,
                "event": r.event,
                "mode": r.mode,
                "symbol": r.symbol,
                "detail": r.detail,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }
