from datetime import datetime, timezone
import httpx

from app.core.config import settings
from app.core.security import create_internal_service_token
from app.execution.execution_engine import engine as execution_engine
from app.execution.order_router import OrderType
from app.monitoring.logging import get_logger, log_event

logger = get_logger("quantx.trading_engine")

class TradingEngine:

    def __init__(self):
        self.symbol = settings.default_symbol
        self._token = create_internal_service_token()

    async def run_cycle(self):

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

            log_event(
                logger,
                message="scheduler_cycle",
                category="scheduler",
                symbol=self.symbol,
                direction=prediction["direction"],
                confidence=prediction["confidence"],
            )

            if (
                prediction["direction"] in ["LONG", "SHORT"]
                and prediction["confidence"] >= settings.confidence_threshold
                and len(positions) < settings.max_open_positions
            ):

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

                log_event(
                    logger,
                    message="scheduler_execution_result",
                    category="scheduler",
                    symbol=self.symbol,
                    reason=result.reason if result.status not in ("FILLED", "PARTIAL") else None,
                )

            else:
                log_event(logger, message="scheduler_no_trade", category="scheduler", symbol=self.symbol)
