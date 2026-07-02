import asyncio
from app.core.config import settings
from app.engine.trading_engine import TradingEngine

RUNNING = False
engine = TradingEngine()

async def trading_loop():
    global RUNNING

    while RUNNING:
        try:
            await engine.run_cycle()
        except Exception as e:
            print("Scheduler error:", repr(e))

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
