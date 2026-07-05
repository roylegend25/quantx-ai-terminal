import time

from fastapi import APIRouter
import httpx
from app.quant.indicators import compute_features
from app.trading.risk_manager import calculate_levels
from app.strategy.ensemble import evaluate as ensemble_evaluate
from app.intelligence import market_intelligence
from app.timeframes.multi_timeframe import evaluate_all as evaluate_all_timeframes
from app.ml.feature_store import store as feature_store
from app.monitoring.logging import get_logger, log_event
from app.monitoring.metrics import PREDICTION_LATENCY
from app.monitoring.tracing import span
from app.risk import settings_repository

router = APIRouter(prefix="/api/prediction", tags=["prediction"])

logger = get_logger("quantx.prediction")

BINANCE_FAPI = "https://fapi.binance.com"

# multi-timeframe consensus only ever nudges confidence - it never changes direction
MTF_AGREE_BOOST_MAX = 15.0
MTF_DISAGREE_PENALTY_MAX = 20.0
MTF_NO_TRADE_PENALTY = 10.0

# How long a computed prediction stays valid before the next request triggers a
# recompute. The frontend's "Next Prediction In" countdown mirrors this exact
# window via `computed_at`, so the two must stay in lockstep - see
# frontend/src/components/Dashboard/PredictionGauge.tsx (CYCLE_SECONDS).
PREDICTION_CACHE_TTL_SECONDS = 60
_prediction_cache: dict[tuple[str, str], dict] = {}


def _consensus_adjustment(consensus: dict | None, direction: str) -> float:
    if not consensus:
        return 0.0

    if consensus["direction"] == "NO_TRADE":
        return -MTF_NO_TRADE_PENALTY

    agreement = max(0.0, min(100.0, consensus.get("agreement") or 0.0)) / 100

    if consensus["direction"] == direction:
        return round(MTF_AGREE_BOOST_MAX * agreement, 1)

    return round(-MTF_DISAGREE_PENALTY_MAX * agreement, 1)


def make_prediction(features: dict, market_context: dict | None = None, consensus: dict | None = None):
    ens = ensemble_evaluate(features, market_context)
    decision = ens["ensemble"]

    consensus_adjustment = _consensus_adjustment(consensus, decision["direction"])
    confidence = round(max(0.0, min(100.0, decision["confidence"] + consensus_adjustment)), 1)

    price = features["price"]
    atr = features.get("atr") or 1

    levels = calculate_levels(
        price,
        atr,
        decision["direction"],
    )

    # Lightweight, dashboard-editable gate (see app/risk/settings_repository.py):
    # reflects whether this specific direction/confidence would currently
    # clear the risk desk, using the same live-configurable limits the
    # auto-trading scheduler reads. It intentionally doesn't factor in
    # portfolio-level state (daily/weekly loss, drawdown, cooldown) since
    # this endpoint has no portfolio context - that fuller picture is what
    # app.trading.risk_manager.evaluate_risk() computes before the scheduler
    # actually routes an order.
    risk_settings = settings_repository.get_settings()
    required_confidence = round(risk_settings["min_confidence_to_trade"] * 100, 1)
    direction = decision["direction"]
    direction_allowed = (
        (direction == "LONG" and risk_settings["allow_long"])
        or (direction == "SHORT" and risk_settings["allow_short"])
    )

    if not risk_settings["paper_trading_enabled"]:
        risk_allowed, risk_reason = False, "Paper trading is disabled in risk settings"
    elif direction == "NO_TRADE":
        risk_allowed, risk_reason = False, "No qualifying trade signal"
    elif not direction_allowed:
        risk_allowed, risk_reason = False, f"{direction.title()} trades disabled by risk settings"
    elif confidence < required_confidence:
        risk_allowed, risk_reason = False, f"Confidence {confidence:.1f}% below required {required_confidence:.1f}%"
    else:
        risk_allowed, risk_reason = True, "Risk checks passed"

    return {
        "direction": decision["direction"],
        "probability_up": decision["probability_up"],
        "probability_down": decision["probability_down"],
        "confidence": confidence,
        "price": round(price, 2),
        "target": levels.take_profit,
        "stop": levels.stop_loss,
        "trailing_stop": levels.trailing_stop,
        "break_even": levels.break_even,
        "regime": ens["regime"],
        "feature_regime": features["regime"],
        "trade_quality": round(confidence / 10, 2),
        "strategies": ens["strategies"],
        "strategy_weights": ens["weights"],
        "market_context": market_context,
        "market_context_adjustment": ens["market_context_adjustment"],
        "multi_timeframe_consensus": consensus,
        "multi_timeframe_adjustment": consensus_adjustment,
        "risk": {
            "allowed": risk_allowed,
            "reason": risk_reason,
            "required_confidence": required_confidence,
            "max_risk_per_trade_pct": risk_settings["max_risk_per_trade_pct"],
        },
        "features": features,
    }

@router.get("/{symbol}")
async def prediction(symbol: str, interval: str = "5m", limit: int = 220):
    symbol = symbol.upper()
    start = time.perf_counter()

    cache_key = (symbol, interval)
    cached = _prediction_cache.get(cache_key)
    if cached and time.time() - cached["computed_at"] / 1000 < PREDICTION_CACHE_TTL_SECONDS:
        return cached["response"]

    with span("prediction", symbol=symbol, interval=interval):
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{BINANCE_FAPI}/fapi/v1/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
            )
            r.raise_for_status()

        candles = [
            {
                "time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            for k in r.json()
        ]

        features = compute_features(candles)["symbol_features"]

        try:
            market_context = await market_intelligence.get_context(symbol)
        except Exception:
            market_context = None

        try:
            consensus = (await evaluate_all_timeframes(symbol, market_context))["consensus"]
        except Exception:
            consensus = None

        pred = make_prediction(features, market_context, consensus)
        pred["computed_at"] = int(time.time() * 1000)

        try:
            pred["feature_id"] = feature_store.save_prediction(
                symbol=symbol,
                timeframe=interval,
                prediction=pred,
            )
        except Exception:
            pred["feature_id"] = None

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    PREDICTION_LATENCY.labels(symbol=symbol).observe(latency_ms / 1000)

    weights = pred.get("strategy_weights") or {}
    dominant_strategy = max(weights, key=weights.get) if weights else None

    log_event(
        logger,
        message="prediction_generated",
        category="prediction",
        endpoint=f"/api/prediction/{symbol}",
        prediction_id=pred.get("feature_id"),
        strategy=dominant_strategy,
        confidence=pred.get("confidence"),
        latency_ms=latency_ms,
        symbol=symbol,
        error=None,
    )

    response = {
        "symbol": symbol,
        "interval": interval,
        "prediction": pred,
    }
    _prediction_cache[cache_key] = {"computed_at": pred["computed_at"], "response": response}
    return response
