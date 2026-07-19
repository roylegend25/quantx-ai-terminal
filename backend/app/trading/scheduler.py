import asyncio
import logging
import time
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
engine = TradingEngine()
logger = get_logger("quantx.scheduler")

async def trading_loop():
    global RUNNING

    while RUNNING:
        start = time.perf_counter()
        try:
            if maintenance.enabled():
                await execution_lease.release()
                log_event(logger, message="scheduler_deployment_maintenance", category="scheduler")
            elif not modes.get_control()["execution_enabled"]:
                await execution_lease.release()
                log_event(logger, message="scheduler_execution_disabled", category="scheduler",
                          reason=modes.get_control()["execution_state"])
            elif (
                modes.effective_mode() == modes.MODE_LIVE
                and binance_time.health(BinanceProduct.USD_M_FUTURES)["status"] != "synced"
            ):
                await execution_lease.release()
                health = binance_time.health(BinanceProduct.USD_M_FUTURES)
                halt_active_verification("binance_timestamp_sync_unsafe")
                log_event(
                    logger, message="scheduler_timestamp_unsafe", level=logging.ERROR,
                    category="scheduler", reason=f"Binance timestamp status {health['status']}",
                    sync_status=health["status"], sample_age_seconds=health["sample_age_seconds"],
                )
            else:
                owns_lease = await execution_lease.acquire_or_renew()
                if not owns_lease:
                    log_event(logger, message="scheduler_execution_lease_unavailable", category="scheduler")
                else:
                    await engine.run_cycle(execution_lease_owner=execution_lease.owner)
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
