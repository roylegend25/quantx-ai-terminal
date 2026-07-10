import asyncio
import logging
import httpx
from app.core.config import settings
from app.core.security import create_internal_service_token
from app.monitoring.logging import get_logger, log_event

API = "http://127.0.0.1:8000"
RUNNING = False
_TOKEN = None
logger = get_logger("quantx.position_manager")


def should_close_position(
    side: str,
    mark: float,
    sl: float | None,
    tp: float | None,
    liquidation_price: float | None = None,
    trailing: bool = False,
) -> tuple[bool, str]:
    """Pure SL/TP/liquidation decision, extracted so it can be exercised
    directly (e.g. by the stress-test harness) without running the live
    polling loop. Returns the canonical close-reason code persisted on the
    trade: LIQUIDATION | STOP_LOSS | TRAILING_STOP | TAKE_PROFIT.

    Liquidation is checked first: on a mark that gapped through several
    levels the worst outcome wins. `trailing` marks the SL as managed by a
    trailing-stop ratchet, so a stop hit is reported as TRAILING_STOP."""
    stop_reason = "TRAILING_STOP" if trailing else "STOP_LOSS"

    if side == "LONG":
        if liquidation_price and mark <= float(liquidation_price):
            return True, "LIQUIDATION"
        if sl and mark <= float(sl):
            return True, stop_reason
        if tp and mark >= float(tp):
            return True, "TAKE_PROFIT"

    if side == "SHORT":
        if liquidation_price and mark >= float(liquidation_price):
            return True, "LIQUIDATION"
        if sl and mark >= float(sl):
            return True, stop_reason
        if tp and mark <= float(tp):
            return True, "TAKE_PROFIT"

    return False, ""


async def manage_positions():
    global RUNNING, _TOKEN

    while RUNNING:
        try:
            if _TOKEN is None:
                _TOKEN = create_internal_service_token()

            headers = {"Authorization": f"Bearer {_TOKEN}"}
            async with httpx.AsyncClient(timeout=20, headers=headers) as client:
                positions = (
                    await client.get(f"{API}/api/paper/positions")
                ).json().get("positions", [])

                for pos in positions:
                    trade_id = pos["id"]
                    side = pos["side"]
                    mark = float(pos["mark"])
                    sl = pos.get("sl")
                    tp = pos.get("tp")

                    should_close, reason = should_close_position(
                        side, mark, sl, tp,
                        liquidation_price=pos.get("liquidation_price"),
                        trailing=bool(pos.get("trailing_stop")),
                    )

                    if should_close:
                        await client.post(f"{API}/api/paper/close/{trade_id}", params={"reason": reason})
                        log_event(
                            logger,
                            message="position_closed_auto",
                            category="trading",
                            trade_id=trade_id,
                            side=side,
                            reason=reason,
                        )

        except Exception as e:
            log_event(logger, message="position_manager_error", level=logging.ERROR, category="scheduler", error=repr(e))

        await asyncio.sleep(settings.position_manager_interval_seconds)

def start_position_manager():
    global RUNNING
    if RUNNING:
        return
    RUNNING = True
    asyncio.create_task(manage_positions())

def stop_position_manager():
    global RUNNING
    RUNNING = False
