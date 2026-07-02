import asyncio

import httpx

from app.quant.indicators import compute_features
from app.strategy.ensemble import evaluate as ensemble_evaluate

BINANCE_FAPI = "https://fapi.binance.com"

TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]


async def fetch_candles(client: httpx.AsyncClient, symbol: str, interval: str, limit: int = 220):
    r = await client.get(
        f"{BINANCE_FAPI}/fapi/v1/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
    )
    r.raise_for_status()

    return [
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


async def evaluate_timeframe(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    market_context: dict | None = None,
) -> dict:
    candles = await fetch_candles(client, symbol, interval)
    features = compute_features(candles)["symbol_features"]
    ens = ensemble_evaluate(features, market_context)
    decision = ens["ensemble"]

    return {
        "direction": decision["direction"],
        "confidence": round(decision["confidence"]),
        "regime": ens["regime"],
        "trend_score": features["trend_score"],
        "rsi": features["rsi"],
        "macd_hist": features["macd_hist"],
    }


def build_consensus(timeframes: dict) -> dict:
    total = len(timeframes)

    counts: dict[str, int] = {}
    for tf in timeframes.values():
        counts[tf["direction"]] = counts.get(tf["direction"], 0) + 1

    top_count = max(counts.values())
    candidates = [direction for direction, count in counts.items() if count == top_count]

    def avg_confidence(direction: str) -> float:
        confidences = [tf["confidence"] for tf in timeframes.values() if tf["direction"] == direction]
        return sum(confidences) / len(confidences) if confidences else 0.0

    direction = max(candidates, key=avg_confidence) if len(candidates) > 1 else candidates[0]

    agreeing = [tf for tf in timeframes.values() if tf["direction"] == direction]
    agreement = round(len(agreeing) / total * 100)
    confidence = round(avg_confidence(direction))

    return {
        "direction": direction,
        "confidence": confidence,
        "agreement": agreement,
        "reason": f"{len(agreeing)} of {total} timeframes support {direction}",
    }


async def evaluate_all(symbol: str, market_context: dict | None = None) -> dict:
    symbol = symbol.upper()

    async with httpx.AsyncClient(timeout=15) as client:
        results = await asyncio.gather(
            *(evaluate_timeframe(client, symbol, interval, market_context) for interval in TIMEFRAMES),
            return_exceptions=True,
        )

    timeframes = {}
    for interval, result in zip(TIMEFRAMES, results):
        if isinstance(result, Exception):
            timeframes[interval] = {
                "direction": "NO_TRADE",
                "confidence": 0,
                "regime": None,
                "trend_score": None,
                "rsi": None,
                "macd_hist": None,
                "error": str(result),
            }
        else:
            timeframes[interval] = result

    return {
        "timeframes": timeframes,
        "consensus": build_consensus(timeframes),
    }
