from datetime import datetime, timezone
import httpx

from app.core.config import settings
from app.core.security import create_internal_service_token
from app.execution.execution_engine import engine as execution_engine
from app.execution.order_router import OrderType

class TradingEngine:

    def __init__(self):
        self.symbol = settings.default_symbol
        self._token = create_internal_service_token()

    async def run_cycle(self):
        print(f"\n========== {datetime.now(timezone.utc)} ==========")

        headers = {"Authorization": f"Bearer {self._token}"}
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:

            prediction = (
                await client.get(
                    f"http://127.0.0.1:8000/api/prediction/{self.symbol}"
                )
            ).json()["prediction"]

            portfolio = (
                await client.get(
                    "http://127.0.0.1:8000/api/paper/portfolio"
                )
            ).json()

            positions = (
                await client.get(
                    "http://127.0.0.1:8000/api/paper/positions"
                )
            ).json()["positions"]

            signal_time = datetime.now(timezone.utc)

            print("Prediction :", prediction["direction"])
            print("Confidence :", prediction["confidence"])
            print("Open Trades:", len(positions))
            print("Equity     :", portfolio["equity"])

            if (
                prediction["direction"] in ["LONG", "SHORT"]
                and prediction["confidence"] >= settings.confidence_threshold
                and len(positions) < settings.max_open_positions
            ):

                print("Routing paper trade through execution engine...")

                result = await execution_engine.submit_order(
                    symbol=self.symbol,
                    side=prediction["direction"],
                    usdt_size=1000,
                    order_type=OrderType.IOC,
                    sl=prediction["stop"],
                    tp=prediction["target"],
                    feature_id=prediction.get("feature_id"),
                    regime=prediction["regime"],
                    strategies=prediction["strategies"],
                    signal_time=signal_time,
                    open_positions=len(positions),
                    equity=portfolio.get("equity"),
                )

                print(
                    f"Execution {result.status}: filled {result.filled_qty}/{result.requested_qty} "
                    f"@ {result.avg_fill_price} (slippage {result.actual_slippage_bps} bps, "
                    f"{result.latency_ms}ms, {result.retries} retries)"
                    if result.status in ("FILLED", "PARTIAL")
                    else f"Execution {result.status}: {result.reason}"
                )

            else:
                print("No trade this cycle.")
