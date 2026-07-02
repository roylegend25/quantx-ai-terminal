import asyncio
import httpx
from datetime import datetime, timezone
from app.core.config import settings
from app.core.security import create_internal_service_token

API = "http://127.0.0.1:8000"
RUNNING = False
_TOKEN = None

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

                    should_close = False
                    reason = ""

                    if side == "LONG":
                        if sl and mark <= float(sl):
                            should_close = True
                            reason = "SL hit"
                        elif tp and mark >= float(tp):
                            should_close = True
                            reason = "TP hit"

                    if side == "SHORT":
                        if sl and mark >= float(sl):
                            should_close = True
                            reason = "SL hit"
                        elif tp and mark <= float(tp):
                            should_close = True
                            reason = "TP hit"

                    if should_close:
                        await client.post(f"{API}/api/paper/close/{trade_id}")
                        print(
                            f"[{datetime.now(timezone.utc)}] Closed trade {trade_id}: {reason}"
                        )

        except Exception as e:
            print("Position Manager error:", repr(e))

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
