import logging
from datetime import datetime, timezone
import httpx

from app.core.config import settings
from app.core.security import create_internal_service_token
from app.execution.order_router import OrderType
from app.monitoring.logging import get_logger, log_event
from app.trading import risk_manager
from app.trading.execution_router import router as execution_router
from app.trading import modes

logger = get_logger("quantx.trading_engine")


def _blocked_outcome(reason: str, *, data_reliable: bool | None) -> str:
    text = (reason or "").lower()
    if data_reliable is False or any(word in text for word in ("data", "stale", "volume", "spread")):
        return "BLOCKED_BY_DATA"
    if any(word in text for word in ("balance", "margin", "notional", "position size")):
        return "BLOCKED_BY_BALANCE"
    if any(word in text for word in ("risk", "loss", "position", "confidence", "duplicate")):
        return "BLOCKED_BY_RISK"
    return "NO_TRADE"


def _record_candidate(symbol: str, outcome: str, reason: str, **detail) -> None:
    payload = {"outcome": outcome, "reason": reason, **detail}
    log_event(logger, message="trade_candidate_outcome", category="trading", symbol=symbol, **payload)
    modes.audit("trade_candidate_outcome", symbol=symbol, detail=payload)

class TradingEngine:

    def __init__(self):
        # Every configured symbol is evaluated every cycle (see
        # settings.symbols / ENABLED_SYMBOLS) - previously this only ever
        # traded settings.default_symbol, which is why ETHUSDT never got a
        # paper trade even though its prediction/risk pipeline worked fine.
        self.symbols = settings.symbols
        self._token = create_internal_service_token()

    async def run_cycle(self, execution_lease_owner: str | None = None):
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
        prediction_response = (
            await client.get(
                f"http://127.0.0.1:8000/api/prediction/{symbol}"
            )
        ).json()
        prediction = prediction_response["prediction"]

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
        data_quality = prediction.get("data_quality") or {}
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
            data_reliable=data_quality.get("reliable"),
            data_reason=data_quality.get("reason"),
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

            # Execution intent goes to the mode-resolving router (Phase 23),
            # never to a provider directly: PAPER books a simulated fill via
            # the existing execution engine, BINANCE_LIVE runs the real risk
            # gate and places an actual order, and a locked/killed state
            # blocks with a reason - all decided per-cycle, no restart.
            result = await execution_router.open_position(
                symbol=symbol,
                side=prediction["direction"],
                notional_usdt=decision["settings"]["max_position_size_usd"],
                clamp_to_max=True,
                order_type=OrderType.IOC,
                sl=prediction["stop"],
                tp=prediction["target"],
                confidence=prediction["confidence"],
                data_reliable=data_quality.get("reliable"),
                spread_pct=spread_pct,
                feature_id=prediction.get("feature_id"),
                regime=prediction["regime"],
                strategies=prediction["strategies"],
                signal_time=signal_time,
                open_positions=len(positions),
                equity=portfolio.get("equity"),
                timeframe=prediction_response.get("timeframe"),
                decision_engine=prediction.get("decision_engine"),
                automated_execution=True,
                execution_lease_owner=execution_lease_owner,
            )

            log_event(
                logger,
                message="scheduler_execution_result",
                category="scheduler",
                symbol=symbol,
                mode=result.mode,
                reason=result.reason if not result.ok else None,
            )
            if result.ok:
                _record_candidate(symbol, "TRADE_APPROVED", "Order accepted", direction=prediction["direction"],
                                  confidence=prediction["confidence"], mode=result.mode)
            else:
                reason = result.reason or "Execution failed"
                lower = reason.lower()
                outcome = ("BLOCKED_BY_BALANCE" if any(x in lower for x in ("balance", "margin", "notional"))
                           else "BLOCKED_BY_EXCHANGE" if result.mode != modes.MODE_PAPER
                           else "EXECUTION_FAILED")
                _record_candidate(symbol, outcome, reason, direction=prediction["direction"],
                                  confidence=prediction["confidence"], mode=result.mode)

        else:
            outcome = _blocked_outcome(decision["reason"], data_reliable=data_quality.get("reliable"))
            _record_candidate(symbol, outcome, decision["reason"], direction=prediction["direction"],
                              confidence=prediction["confidence"], mode=modes.effective_mode())
            log_event(
                logger, message="scheduler_no_trade", category="scheduler",
                symbol=symbol, reason=decision["reason"],
            )
