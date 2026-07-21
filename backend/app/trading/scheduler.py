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

def start_scheduler():
    global RUNNING

    if RUNNING:
        return

    RUNNING = True
    log_event(logger, message="scheduler_started", category="scheduler")
    asyncio.create_task(trading_loop())

def stop_scheduler():
    global RUNNING
    RUNNING = False
    log_event(logger, message="scheduler_stopped", category="scheduler")
