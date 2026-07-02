from fastapi import APIRouter
import httpx
from app.quant.indicators import compute_features
from app.trading.risk_manager import calculate_levels
from app.strategy.ensemble import evaluate as ensemble_evaluate
from app.intelligence import market_intelligence
from app.timeframes.multi_timeframe import evaluate_all as evaluate_all_timeframes

router = APIRouter(prefix="/api/prediction", tags=["prediction"])

BINANCE_FAPI = "https://fapi.binance.com"

# multi-timeframe consensus only ever nudges confidence - it never changes direction
MTF_AGREE_BOOST_MAX = 15.0
MTF_DISAGREE_PENALTY_MAX = 20.0
MTF_NO_TRADE_PENALTY = 10.0


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
            "allowed": decision["direction"] != "NO_TRADE" and confidence >= 70,
            "reason": "Risk checks passed" if confidence >= 70 else "Confidence below threshold",
            "max_risk_per_trade_pct": 0.5,
        },
        "features": features,
    }

@router.get("/{symbol}")
async def prediction(symbol: str, interval: str = "5m", limit: int = 220):
    symbol = symbol.upper()

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

    return {
        "symbol": symbol,
        "interval": interval,
        "prediction": make_prediction(features, market_context, consensus),
    }
