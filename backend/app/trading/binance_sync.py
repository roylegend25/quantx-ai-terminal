"""Background position/order sync for real Binance modes (Phase 22).

Every tick: if the effective mode is BINANCE_TESTNET or BINANCE_LIVE, pull
positions from Binance through the execution router and mirror them into
exchange_positions. In PAPER (or locked) mode the loop is a cheap no-op -
it never constructs a client, so a paper-only deployment makes zero calls
to Binance account endpoints.

Binance is the source of truth: sync REPLACES the local mirror wholesale
(see BinanceExecutionProvider.sync_positions). A failed sync is logged and
retried next tick - it never raises out of the loop.
"""

import asyncio
import logging

from app.monitoring.logging import get_logger, log_event
from app.trading import modes
from app.trading.execution_router import router as execution_router

SYNC_INTERVAL_SECONDS = 30
RUNNING = False
logger = get_logger("quantx.binance_sync")


async def sync_loop():
    global RUNNING
    while RUNNING:
        try:
            if modes.effective_mode() in modes.REAL_MODES:
                result = await execution_router.sync_positions()
                if not result.ok:
                    log_event(
                        logger,
                        message="binance_sync_failed",
                        level=logging.WARNING,
                        category="trading",
                        error=result.reason,
                    )
        except Exception as e:
            log_event(logger, message="binance_sync_error", level=logging.ERROR, category="trading", error=repr(e))
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


def start_binance_sync():
    global RUNNING
    if RUNNING:
        return
    RUNNING = True
    asyncio.create_task(sync_loop())


def stop_binance_sync():
    global RUNNING
    RUNNING = False
