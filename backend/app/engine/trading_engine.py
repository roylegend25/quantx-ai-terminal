from datetime import datetime, timezone
import httpx

from app.core.config import settings

class TradingEngine:

    def __init__(self):
        self.symbol = settings.default_symbol

    async def run_cycle(self):
        print(f"\n========== {datetime.now(timezone.utc)} ==========")

        async with httpx.AsyncClient(timeout=20) as client:

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

            print("Prediction :", prediction["direction"])
            print("Confidence :", prediction["confidence"])
            print("Open Trades:", len(positions))
            print("Equity     :", portfolio["equity"])

            if (
                prediction["direction"] in ["LONG", "SHORT"]
                and prediction["confidence"] >= settings.confidence_threshold
                and len(positions) < settings.max_open_positions
            ):

                print("Opening paper trade...")

                await client.post(
                    "http://127.0.0.1:8000/api/paper/open",
                    params={
                        "symbol": self.symbol,
                        "side": prediction["direction"],
                        "usdt_size": 1000,
                        "sl": prediction["stop"],
                        "tp": prediction["target"],
                    },
                )

                print("Trade opened.")

            else:
                print("No trade this cycle.")
