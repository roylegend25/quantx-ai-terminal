"""Prediction Results tab + resolver-health API (unresolved-pipeline rebuild).

Read-only for every route except POST /catchup, which only ever triggers the
resolver's existing backfill+resolve cycle (app/decision_engine/resolver.py) -
it can never create, modify, or cancel a trade, and never touches the
trading mode/lease/execution path.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import SessionLocal
from app.decision_engine import resolver_status
from app.decision_engine.resolver import backfill_overdue_candles, resolve_due
from app.monitoring.logging import get_logger, log_event
from app.trading import modes

router = APIRouter(prefix="/api/predictions", tags=["prediction-results"])
logger = get_logger("quantx.prediction_results")

_last_manual_catchup_at: float = 0.0
_MANUAL_CATCHUP_MIN_INTERVAL_SECONDS = 30.0
_catchup_running = False


async def _admin(request: Request, user: str = Depends(get_current_user)) -> str:
    if user != settings.admin_username:
        modes.audit("prediction_catchup_unauthorized_attempt", detail={"subject": user})
        log_event(logger, message="prediction_catchup_unauthorized_attempt", level=logging.WARNING, category="risk", subject=user)
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


@router.get("/resolver/health")
def resolver_health():
    db = SessionLocal()
    try:
        progress = resolver_status.catchup_progress(db)
    finally:
        db.close()
    healthy = progress["last_error"] is None and (
        progress["oldest_overdue_age_seconds"] is None or progress["oldest_overdue_age_seconds"] < 86400 or progress["total_overdue"] == 0
    )
    return {**progress, "healthy": bool(healthy), "provider_health": resolver_status.provider_health()}


@router.get("/unresolved-summary")
def unresolved_summary(symbol: str | None = Query(default=None)):
    db = SessionLocal()
    try:
        return resolver_status.unresolved_reason_summary(db, symbol)
    finally:
        db.close()


@router.get("/catchup-progress")
def catchup_progress():
    db = SessionLocal()
    try:
        return resolver_status.catchup_progress(db)
    finally:
        db.close()


@router.get("/results/latest")
def results_latest(
    limit: int = Query(default=10, ge=1, le=200),
    symbol: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    model: str | None = Query(default=None),
    resolved: bool | None = Query(default=None),
    outcome: str | None = Query(default=None),
    source_exchange: str | None = Query(default=None),
):
    if symbol and symbol.upper() not in ("BTC", "ETH", "BTCUSDT", "ETHUSDT"):
        raise HTTPException(status_code=400, detail="symbol must be BTC, ETH, BTCUSDT, or ETHUSDT")
    normalized_symbol = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}.get((symbol or "").upper(), symbol.upper() if symbol else None)
    db = SessionLocal()
    try:
        return {"results": resolver_status.latest_results(
            db, limit=limit, symbol=normalized_symbol, timeframe=timeframe,
            source_name=model, resolved=resolved, outcome=outcome, source_exchange=source_exchange,
        )}
    finally:
        db.close()


@router.get("/accuracy-summary")
def accuracy_summary():
    db = SessionLocal()
    try:
        return resolver_status.accuracy_summary(db)
    finally:
        db.close()


@router.get("/provider-health")
def provider_health():
    return resolver_status.provider_health()


@router.post("/resolver/catchup")
async def trigger_catchup(request: Request, limit: int = Query(default=100, ge=1, le=200), admin: str = Depends(_admin)):
    """Bounded, idempotent, admin-only, rate-limited manual catch-up trigger.
    Runs the existing backfill_overdue_candles + resolve_due cycle (the same
    two calls the scheduler makes every 60s) - cannot create a trade.

    Capped well below the scheduler's own pace deliberately: each backfilled
    row can make a real (up to resolver_provider_timeout-bounded) network
    call inside one long-lived DB session, so a large limit here holds a
    single SQLite write transaction open long enough to visibly stall other
    read traffic. Observed directly during rollout verification: a limit=500
    manual trigger blocked the dashboard's read endpoints for several
    minutes. Prefer letting the 60s scheduled cycle (smaller default limits)
    catch up gradually over forcing a huge one-shot batch here."""
    global _last_manual_catchup_at, _catchup_running

    if _catchup_running:
        raise HTTPException(status_code=409, detail="A catch-up cycle is already running")
    now = time.monotonic()
    if now - _last_manual_catchup_at < _MANUAL_CATCHUP_MIN_INTERVAL_SECONDS:
        retry_after = round(_MANUAL_CATCHUP_MIN_INTERVAL_SECONDS - (now - _last_manual_catchup_at), 1)
        raise HTTPException(status_code=429, detail=f"Rate limited - retry in {retry_after}s")

    _catchup_running = True
    _last_manual_catchup_at = now
    modes.audit("prediction_catchup_triggered", detail={"admin": admin, "limit": limit})
    db = SessionLocal()
    try:
        backfilled = await backfill_overdue_candles(db, limit=limit)
        resolved = resolve_due(db, limit=limit)
        result = {"backfilled": backfilled, "resolved": resolved}
        modes.audit("prediction_catchup_completed", detail={"admin": admin, **result})
        log_event(logger, message="prediction_catchup_manual", category="prediction", admin=admin, **result)
        return result
    finally:
        db.close()
        _catchup_running = False
