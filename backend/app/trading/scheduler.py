import asyncio
import logging
import time
from datetime import datetime, timezone
from app.core.config import settings
from app.engine.trading_engine import TradingEngine
from app.monitoring.logging import get_logger, log_event
from app.monitoring.metrics import SCHEDULER_CYCLE_LATENCY
from app.deployment import maintenance
from app.deployment.lease import execution_lease
from app.trading import modes
from app.exchanges.binance_time import BinanceProduct, binance_time
from app.trading.safety_halt import halt_active_verification

RUNNING = False
OBSERVATION_RUNNING = False
LAST_CYCLE_AT: str | None = None
engine = TradingEngine()
logger = get_logger("quantx.scheduler")


async def _renew_execution_lease(stop: asyncio.Event) -> None:
    """Keep the single-executor lease valid for the whole strategy cycle.

    Strategy evaluation can legitimately take longer than the execution lease TTL.
    Renewal is deliberately fail-closed: the router's final ``owns`` check will
    reject an entry if this heartbeat ever loses authority.
    """
    interval = max(1.0, execution_lease.ttl / 3)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            if not await execution_lease.renew():
                log_event(
                    logger,
                    message="scheduler_execution_lease_renewal_failed",
                    level=logging.ERROR,
                    category="scheduler",
                )
                return


async def _run_engine_cycle() -> None:
    owns_lease = await execution_lease.acquire_or_renew()
    if not owns_lease:
        log_event(logger, message="scheduler_execution_lease_unavailable", category="scheduler")
        return
    heartbeat_stop = asyncio.Event()
    heartbeat = asyncio.create_task(_renew_execution_lease(heartbeat_stop))
    try:
        await engine.run_cycle(execution_lease_owner=execution_lease.owner)
    finally:
        heartbeat_stop.set()
        await heartbeat

async def trading_loop():
    global RUNNING, LAST_CYCLE_AT

    while RUNNING:
        start = time.perf_counter()
        LAST_CYCLE_AT = datetime.now(timezone.utc).isoformat()
        try:
            if maintenance.enabled():
                await execution_lease.release()
                log_event(logger, message="scheduler_deployment_maintenance", category="scheduler")
            elif not modes.get_control()["execution_enabled"]:
                await execution_lease.release()
                log_event(logger, message="scheduler_execution_disabled", category="scheduler",
                          reason=modes.get_control()["execution_state"])
            elif modes.effective_mode() == modes.MODE_LIVE:
                health = binance_time.health(BinanceProduct.USD_M_FUTURES)
                if health["status"] != "synced":
                    try:
                        health = await binance_time.refresh(
                            BinanceProduct.USD_M_FUTURES,
                            reason="scheduler_cycle_preflight",
                        )
                    except Exception as exc:
                        health = binance_time.health(BinanceProduct.USD_M_FUTURES)
                        log_event(
                            logger, message="scheduler_timestamp_refresh_failed",
                            level=logging.ERROR, category="scheduler", error=repr(exc),
                        )
                if health["status"] != "synced":
                    await execution_lease.release()
                    halt_active_verification("binance_timestamp_sync_unsafe")
                    log_event(
                        logger, message="scheduler_timestamp_unsafe", level=logging.ERROR,
                        category="scheduler", reason=f"Binance timestamp status {health['status']}",
                        sync_status=health["status"], sample_age_seconds=health["sample_age_seconds"],
                    )
                else:
                    await _run_engine_cycle()
            else:
                await _run_engine_cycle()
        except Exception as e:
            log_event(logger, message="scheduler_cycle_error", level=logging.ERROR, category="scheduler", error=repr(e))
        finally:
            SCHEDULER_CYCLE_LATENCY.observe(time.perf_counter() - start)

        await asyncio.sleep(settings.scheduler_interval_seconds)

async def _observation_cycle() -> None:
    """Compute+persist (never execute) an Active Drive V2 decision for every
    configured prediction_supported timeframe that's due for a fresh one -
    not just the single execution timeframe trading_loop covers. This is
    what lets GET /api/prediction/{symbol} be strictly read-only for any
    timeframe a dashboard requests (main-purpose consolidation, Stage 2)."""
    from app.core.security import INTERNAL_SERVICE_SUBJECT
    from app.db.session import SessionLocal
    from app.db.models import ActiveDriveDecision
    from app.timeframes.canonical import TIMEFRAME_CAPABILITIES, calendar_month_age
    from app.api.prediction import compute_and_persist_prediction
    from app.decision_engine.router import decision_engine_router
    from app.decision_engine.repository import owner

    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    owner_id = owner(INTERNAL_SERVICE_SUBJECT)

    for symbol in settings.symbols:
        for tf, caps in TIMEFRAME_CAPABILITIES.items():
            if not caps.prediction_supported:
                continue
            db = SessionLocal()
            try:
                active_engine = decision_engine_router.get_active_engine(db, INTERNAL_SERVICE_SUBJECT)
                latest = (
                    db.query(ActiveDriveDecision)
                    .filter(
                        ActiveDriveDecision.user_id == owner_id,
                        ActiveDriveDecision.symbol == symbol,
                        ActiveDriveDecision.timeframe == tf,
                        ActiveDriveDecision.engine == active_engine.name.value,
                        ActiveDriveDecision.shadow.is_(False),
                    )
                    .order_by(ActiveDriveDecision.created_at.desc())
                    .first()
                )
            finally:
                db.close()

            due = latest is None
            if latest is not None:
                last_ms = int(latest.created_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
                if tf == "1M":
                    due = calendar_month_age(last_ms, now_ms) >= 1
                else:
                    due = (now_ms - last_ms) >= (caps.fixed_duration_ms or 60_000)

            if not due:
                continue
            try:
                await compute_and_persist_prediction(symbol, interval=tf, current_user=INTERNAL_SERVICE_SUBJECT)
            except Exception as e:
                log_event(
                    logger, message="observation_cycle_error", level=logging.ERROR, category="scheduler",
                    symbol=symbol, timeframe=tf, error=repr(e),
                )


async def observation_loop():
    while OBSERVATION_RUNNING:
        try:
            await _observation_cycle()
        except Exception as e:
            log_event(logger, message="observation_loop_error", level=logging.ERROR, category="scheduler", error=repr(e))
        await asyncio.sleep(settings.observation_interval_seconds)


def start_scheduler():
    global RUNNING, OBSERVATION_RUNNING

    if RUNNING:
        return

    RUNNING = True
    OBSERVATION_RUNNING = True
    log_event(logger, message="scheduler_started", category="scheduler")
    asyncio.create_task(trading_loop())
    asyncio.create_task(observation_loop())

def stop_scheduler():
    global RUNNING, OBSERVATION_RUNNING
    RUNNING = False
    OBSERVATION_RUNNING = False
    log_event(logger, message="scheduler_stopped", category="scheduler")
