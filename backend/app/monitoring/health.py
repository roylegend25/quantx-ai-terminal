"""Liveness/readiness probes, the Prometheus scrape endpoint, and the
richer JSON status document consumed by the frontend's System Status page.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import psutil
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.models import MLModelRegistry, Portfolio, PredictionFeature, Trade
from app.db.session import get_db
from app.monitoring import metrics
from app.monitoring.logging import get_logger
from app.strategy.manager import STRATEGIES
from app.trading import scheduler as scheduler_module
from app.deployment import maintenance
from app.deployment.lease import execution_lease
from app.core.config import settings
from app.exchanges.binance_time import BinanceProduct, binance_time

logger = get_logger("quantx.health")
router = APIRouter(tags=["health"])

_PROCESS_START = time.time()
_PROCESS = psutil.Process(os.getpid())

# Alert thresholds
HIGH_LATENCY_MS = 2000.0
NO_PREDICTION_ALERT_MINUTES = 30


def _check_database(db: Session) -> tuple[bool, str | None]:
    try:
        db.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:
        return False, repr(exc)


def _check_redis() -> tuple[bool, str | None]:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return False, "REDIS_URL not configured"
    try:
        import redis

        client = redis.Redis.from_url(redis_url, socket_connect_timeout=2)
        client.ping()
        return True, None
    except Exception as exc:
        return False, repr(exc)


def _isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


@router.get("/api/health/live")
async def live():
    """Process is up and able to answer HTTP requests. Never checks
    dependencies - that's what /ready is for."""
    return {"status": "alive", "time": datetime.now(timezone.utc).isoformat()}


@router.get("/api/health/ready")
async def ready(response: Response, db: Session = Depends(get_db)):
    """Process is up AND its hard dependency (the database) is reachable."""
    db_ok, db_err = _check_database(db)
    redis_ok, redis_err = _check_redis()
    futures_time = binance_time.health(BinanceProduct.USD_M_FUTURES)

    is_ready = db_ok
    response.status_code = 200 if is_ready else 503

    return {
        "status": "ready" if is_ready else "not_ready",
        "checks": {
            "database": {"ok": db_ok, "error": db_err},
            "redis": {"ok": redis_ok, "error": redis_err},
            "binance_time": futures_time,
        },
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/metrics")
async def metrics_endpoint(db: Session = Depends(get_db)):
    metrics.refresh_state_gauges(db)
    payload, content_type = metrics.render_latest()
    return Response(content=payload, media_type=content_type)


@router.get("/api/health/status")
async def status(db: Session = Depends(get_db), _user: str = Depends(get_current_user)):
    """Rich JSON status document for the System Status page. Protected like
    every other data endpoint in this app (unlike /live, /ready and
    /metrics, which have to stay open for probes/scrapers that carry no
    auth token)."""

    db_ok, db_err = _check_database(db)
    redis_ok, redis_err = _check_redis()
    futures_time = binance_time.health(BinanceProduct.USD_M_FUTURES)

    last_prediction = (
        db.query(PredictionFeature.timestamp)
        .order_by(PredictionFeature.timestamp.desc())
        .first()
    )
    last_trade = db.query(Trade.opened_at).order_by(Trade.opened_at.desc()).first()
    last_model = (
        db.query(MLModelRegistry.trained_at)
        .order_by(MLModelRegistry.trained_at.desc())
        .first()
    )

    last_prediction_time = last_prediction[0] if last_prediction else None
    last_paper_trade_time = last_trade[0] if last_trade else None
    last_model_training_time = last_model[0] if last_model else None

    portfolio = db.get(Portfolio, 1)

    scheduler_running = scheduler_module.RUNNING
    avg_latency_ms = metrics.recent_avg_latency_ms()

    cpu_percent = psutil.cpu_percent(interval=None)
    memory = psutil.virtual_memory()
    process_memory_mb = round(_PROCESS.memory_info().rss / (1024 * 1024), 1)
    uptime_seconds = round(time.time() - _PROCESS_START, 1)

    now = datetime.now(timezone.utc)

    alerts: list[dict] = []

    if avg_latency_ms is not None and avg_latency_ms > HIGH_LATENCY_MS:
        alerts.append({
            "level": "warning",
            "message": f"High API latency: {avg_latency_ms}ms (threshold {HIGH_LATENCY_MS}ms)",
        })

    if not scheduler_running:
        alerts.append({"level": "critical", "message": "Background scheduler is stopped"})

    if last_prediction_time is None:
        alerts.append({"level": "warning", "message": "No predictions recorded yet"})
    else:
        lp = last_prediction_time if last_prediction_time.tzinfo else last_prediction_time.replace(tzinfo=timezone.utc)
        if now - lp > timedelta(minutes=NO_PREDICTION_ALERT_MINUTES):
            alerts.append({
                "level": "warning",
                "message": f"No predictions in the last {NO_PREDICTION_ALERT_MINUTES} minutes",
            })

    if not db_ok:
        alerts.append({"level": "critical", "message": f"Database unavailable: {db_err}"})

    if not redis_ok:
        alerts.append({"level": "warning", "message": f"Redis unavailable: {redis_err}"})

    if futures_time["status"] != "synced":
        alerts.append({
            "level": "critical",
            "message": f"Binance timestamp synchronization is {futures_time['status']}",
        })

    return {
        "backend": {"status": "up", "time": now.isoformat()},
        "database": {"ok": db_ok, "error": db_err},
        "redis": {"ok": redis_ok, "error": redis_err},
        "binance_time": futures_time,
        "scheduler": {"running": scheduler_running, "execution_lease": execution_lease.public_status()},
        "deployment": {
            **maintenance.status(),
            "application_version": "2.0.0",
            "git_commit": settings.app_git_sha,
            "image_tag": settings.app_image_tag,
            "container_id": os.getenv("HOSTNAME"),
            "startup_time": datetime.fromtimestamp(_PROCESS_START, tz=timezone.utc).isoformat(),
        },
        "last_prediction_time": _isoformat(last_prediction_time),
        "last_paper_trade_time": _isoformat(last_paper_trade_time),
        "last_model_training_time": _isoformat(last_model_training_time),
        "active_strategies": list(STRATEGIES.keys()),
        "api_latency_ms": avg_latency_ms,
        "uptime_seconds": uptime_seconds,
        "cpu_percent": cpu_percent,
        "memory": {
            "system_percent": memory.percent,
            "process_mb": process_memory_mb,
        },
        "portfolio": {
            "win_rate": round((portfolio.wins / max(1, portfolio.wins + portfolio.losses)) * 100, 2) if portfolio else None,
            "total_pnl": round(portfolio.total_pnl, 2) if portfolio else None,
        },
        "alerts": alerts,
    }
