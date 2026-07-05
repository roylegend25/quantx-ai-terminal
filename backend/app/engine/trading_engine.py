import logging
from datetime import datetime, timezone
import httpx

from app.core.config import settings
from app.core.security import create_internal_service_token
from app.execution.execution_engine import engine as execution_engine
from app.execution.order_router import OrderType
from app.monitoring.logging import get_logger, log_event
from app.trading import risk_manager

logger = get_logger("quantx.trading_engine")

class TradingEngine:

    def __init__(self):
        # Every configured symbol is evaluated every cycle (see
        # settings.symbols / ENABLED_SYMBOLS) - previously this only ever
        # traded settings.default_symbol, which is why ETHUSDT never got a
        # paper trade even though its prediction/risk pipeline worked fine.
        self.symbols = settings.symbols
        self._token = create_internal_service_token()

    async def run_cycle(self):
        headers = {"Authorization": f"Bearer {self._token}"}
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            for symbol in self.symbols:
                # One symbol's failure (bad market data, a timed-out
                # prediction call, ...) must not stop the rest from being
                # evaluated this cycle.
                try:
                    await self._run_symbol_cycle(client, symbol)
                except Exception as e:
                    log_event(
                        logger,
                        message="scheduler_symbol_error",
                        level=logging.ERROR,
                        category="scheduler",
                        symbol=symbol,
                        error=repr(e),
                    )

    async def _run_symbol_cycle(self, client: httpx.AsyncClient, symbol: str):
        prediction = (
            await client.get(
                f"http://127.0.0.1:8000/api/prediction/{symbol}"
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

        trade_history = (
            await client.get(
                "http://127.0.0.1:8000/api/paper/history"
            )
        ).json().get("trades", [])

        # Best-effort: a malformed/empty order book (see the
        # bad_orderbook_response stress scenario) must never block a
        # cycle - spread_pct just stays None and evaluate_risk() fails
        # open on that one check, same as any other unavailable input.
        spread_pct = None
        try:
            book = (
                await client.get(f"http://127.0.0.1:8000/api/orderbook/{symbol}")
            ).json()
            bids, asks = book.get("bids"), book.get("asks")
            if bids and asks and bids[0]["price"] > 0:
                spread_pct = (asks[0]["price"] - bids[0]["price"]) / bids[0]["price"] * 100
        except Exception:
            spread_pct = None

        features = prediction.get("features") or {}
        signal_time = datetime.now(timezone.utc)

        decision = risk_manager.evaluate_risk(
            confidence=prediction["confidence"],
            direction=prediction["direction"],
            open_positions=len(positions),
            portfolio=portfolio,
            trade_history=trade_history,
            spread_pct=spread_pct,
            volume=features.get("volume"),
            volume_sma20=features.get("volume_sma20"),
        )

        log_event(
            logger,
            message="scheduler_cycle",
            category="scheduler",
            symbol=symbol,
            direction=prediction["direction"],
            confidence=prediction["confidence"],
            reason=decision["reason"],
        )

        if decision["allowed"]:

            result = await execution_engine.submit_order(
                symbol=symbol,
                side=prediction["direction"],
                usdt_size=decision["settings"]["max_position_size_usd"],
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
                symbol=symbol,
                reason=result.reason if result.status not in ("FILLED", "PARTIAL") else None,
            )

        else:
            log_event(
                logger, message="scheduler_no_trade", category="scheduler",
                symbol=symbol, reason=decision["reason"],
            )
