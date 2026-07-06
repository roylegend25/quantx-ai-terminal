"""Optional background refresh loop for the data engine.

Disabled by default (DATA_SCHEDULER_ENABLED=true to turn on) so enabling
Phase 20 never changes the behavior of an existing deployment by itself.
When enabled it keeps a small, fixed diet of datasets fresh: recent candles
for the configured symbols across the core timeframes, funding, open
interest and daily sentiment. Every run goes through the same validated
downloader path as a manual download - same jobs, same quality reports.
"""

import asyncio
import os

from app.core.config import settings
from app.data_sources import downloader
from app.monitoring.logging import get_logger

logger = get_logger("quantx.data.scheduler")

REFRESH_TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]
_task: asyncio.Task | None = None


def enabled() -> bool:
    return os.getenv("DATA_SCHEDULER_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def interval_seconds() -> int:
    try:
        return max(300, int(os.getenv("DATA_REFRESH_INTERVAL_SECONDS", "900")))
    except ValueError:
        return 900


async def refresh_once() -> None:
    for symbol in settings.symbols:
        for timeframe in REFRESH_TIMEFRAMES:
            try:
                job = downloader.create_job(data_type="candles", symbol=symbol, timeframe=timeframe, limit=500)
                await downloader.execute_job(job["job_id"])
            except Exception as exc:  # noqa: BLE001 - one bad dataset must not stop the sweep
                logger.warning("scheduled candle refresh %s/%s failed: %s", symbol, timeframe, exc)
        for data_type in ("funding", "open_interest"):
            try:
                job = downloader.create_job(data_type=data_type, symbol=symbol, timeframe="5m", limit=500)
                await downloader.execute_job(job["job_id"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("scheduled %s refresh %s failed: %s", data_type, symbol, exc)
    try:
        job = downloader.create_job(data_type="sentiment", limit=30)
        await downloader.execute_job(job["job_id"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduled sentiment refresh failed: %s", exc)


async def _loop() -> None:
    while True:
        try:
            await refresh_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("data refresh sweep failed: %s", exc)
        await asyncio.sleep(interval_seconds())


def start_data_scheduler() -> None:
    global _task
    if not enabled():
        logger.info("data scheduler disabled (set DATA_SCHEDULER_ENABLED=true to enable)")
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())
        logger.info("data scheduler started (every %ss)", interval_seconds())
