import asyncio
import time
from app.core.config import settings
from app.engine.trading_engine import TradingEngine
from app.monitoring.metrics import SCHEDULER_CYCLE_LATENCY

RUNNING = False
engine = TradingEngine()

async def trading_loop():
    global RUNNING

    while RUNNING:
        start = time.perf_counter()
        try:
            await engine.run_cycle()
        except Exception as e:
            print("Scheduler error:", repr(e))
        finally:
            SCHEDULER_CYCLE_LATENCY.observe(time.perf_counter() - start)

        await asyncio.sleep(settings.scheduler_interval_seconds)

def start_scheduler():
    global RUNNING

    if RUNNING:
        return

    RUNNING = True
    asyncio.create_task(trading_loop())

def stop_scheduler():
    global RUNNING
    RUNNING = False
