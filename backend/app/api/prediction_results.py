"""Prediction Results tab + resolver-health API (unresolved-pipeline rebuild).

Read-only for every route except POST /catchup, which only ever triggers the
resolver's existing backfill/resolve cycle (app/decision_engine/resolver.py) -
it can never create, modify, or cancel a trade, and it never touches the
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
from app.decision_engine.resolver import resolve_due
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
    healthy = progress["last_error"] is None and progress["oldest_overdue_age_seconds"] is not None and \
        (progress["oldest_overdue_age_seconds"] < 86400 or progress["total_overdue"] == 0)
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


@router.get("/lifecycle-health")
def lifecycle_health():
    """Explicit lifecycle-status dashboard view (PENDING/RESOLVING/
    RESOLVED_*/VOID_*/RESOLUTION_ERROR_RETRYING) plus independent
    recent/historical queue worker health."""
    db = SessionLocal()
    try:
        return resolver_status.lifecycle_health(db)
    finally:
        db.close()


@router.get("/calibration-dataset")
async def calibration_dataset(admin: str = Depends(_admin)):
    """Trustworthy-outcomes-only calibration metrics (app.decision_engine.
    calibration) - read-only, admin-gated. Never triggers a weight change."""
    from app.decision_engine import calibration
    db = SessionLocal()
    try:
        return calibration.calibration_dataset(db)
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
    normalized_symbol = None
    if symbol:
        normalized_symbol = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}.get(symbol.upper(), symbol.upper())
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
async def trigger_catchup(request: Request, limit: int = Query(default=200, ge=1, le=2000), admin: str = Depends(_admin)):
    """Bounded, idempotent, admin-only, rate-limited manual catch-up trigger.
    Only calls the existing resolve_due cycle - cannot create a trade."""
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
        stats = await resolve_due(db, limit=limit)
        modes.audit("prediction_catchup_completed", detail={"admin": admin, **{k: v for k, v in stats.items() if k != "by_reason"}})
        log_event(logger, message="prediction_catchup_manual", category="prediction", admin=admin, **{k: v for k, v in stats.items() if k != "by_reason"})
        return stats
    finally:
        db.close()
        _catchup_running = False
