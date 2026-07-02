from fastapi import APIRouter
import httpx
from app.quant.indicators import compute_features
from app.trading.risk_manager import calculate_levels
from app.strategy.ensemble import evaluate as ensemble_evaluate

router = APIRouter(prefix="/api/prediction", tags=["prediction"])

BINANCE_FAPI = "https://fapi.binance.com"

def make_prediction(features: dict):
    ens = ensemble_evaluate(features)
    decision = ens["ensemble"]

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
        "confidence": decision["confidence"],
        "price": round(price, 2),
        "target": levels.take_profit,
        "stop": levels.stop_loss,
        "trailing_stop": levels.trailing_stop,
        "break_even": levels.break_even,
        "regime": ens["regime"],
        "feature_regime": features["regime"],
        "trade_quality": round(decision["confidence"] / 10, 2),
        "strategies": ens["strategies"],
        "risk": {
            "allowed": decision["direction"] != "NO_TRADE" and decision["confidence"] >= 70,
            "reason": "Risk checks passed" if decision["confidence"] >= 70 else "Confidence below threshold",
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

    return {
        "symbol": symbol,
        "interval": interval,
        "prediction": make_prediction(features),
    }
