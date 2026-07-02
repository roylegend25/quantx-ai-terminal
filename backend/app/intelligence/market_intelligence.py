import asyncio
import time

from app.intelligence import (
    fear_greed,
    funding,
    liquidations,
    news_sentiment,
    open_interest,
    orderflow,
    whale_tracker,
)

CACHE_TTL_SECONDS = 30
BIAS_NEUTRAL_BAND = 0.15

_cache: dict = {}


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(value, high))


async def _collect(symbol: str) -> dict:
    """Runs every collector with per-source failure isolation, mirroring
    StrategyManager.evaluate's pattern of never letting one bad source take
    the whole context down."""

    async def safe(coro, fallback: dict) -> dict:
        try:
            return await coro
        except Exception as e:
            return {**fallback, "error": str(e)}

    (
        funding_data, oi_data, flow_data, whale_data,
        liquidation_data, fear_greed_data, news_data,
    ) = await asyncio.gather(
        safe(funding.fetch(symbol), {"funding_rate": None}),
        safe(open_interest.fetch(symbol), {"open_interest": None, "oi_change_pct": None}),
        safe(orderflow.fetch(symbol), {
            "long_short_ratio": None, "buy_sell_ratio": None, "buy_volume": None,
            "sell_volume": None, "bid_ask_ratio": None, "cvd": None,
        }),
        safe(whale_tracker.fetch(symbol), {
            "whale_trade_count": 0, "whale_net_flow": 0.0, "whale_bias": "NEUTRAL",
        }),
        safe(liquidations.fetch(symbol), {
            "liquidation_count": 0, "liquidation_notional": 0.0, "liquidation_pressure": "NEUTRAL",
        }),
        safe(fear_greed.fetch(), {"fear_greed_index": None, "fear_greed_label": None}),
        safe(news_sentiment.fetch(), {"news_sentiment": 0.0, "status": "unavailable"}),
    )

    return {
        **funding_data,
        **oi_data,
        **flow_data,
        "whale_activity": {
            "count": whale_data.get("whale_trade_count", 0),
            "net_flow": whale_data.get("whale_net_flow", 0.0),
            "bias": whale_data.get("whale_bias", "NEUTRAL"),
        },
        "liquidation_pressure": {
            "count": liquidation_data.get("liquidation_count", 0),
            "notional": liquidation_data.get("liquidation_notional", 0.0),
            "bias": liquidation_data.get("liquidation_pressure", "NEUTRAL"),
        },
        "fear_greed": {
            "index": fear_greed_data.get("fear_greed_index"),
            "label": fear_greed_data.get("fear_greed_label"),
        },
        "news_sentiment": news_data.get("news_sentiment", 0.0),
    }


def _compute_bias(context: dict) -> tuple[str, float]:
    """Blends every signal into a single [-1, 1] bias score, then buckets it
    into BULLISH / BEARISH / NEUTRAL with a dead zone to avoid noise flips."""
    components = []

    funding_rate = context.get("funding_rate")
    if funding_rate is not None:
        components.append(_clamp(funding_rate / 0.0005))

    oi_change_pct = context.get("oi_change_pct")
    cvd = context.get("cvd")
    if oi_change_pct is not None and cvd is not None and oi_change_pct > 0:
        direction = 1.0 if cvd > 0 else (-1.0 if cvd < 0 else 0.0)
        components.append(direction * _clamp(oi_change_pct / 5.0, 0.0, 1.0))

    long_short_ratio = context.get("long_short_ratio")
    if long_short_ratio is not None:
        # crowded positioning is read as a contrarian signal
        components.append(-_clamp(long_short_ratio - 1.0))

    bid_ask_ratio = context.get("bid_ask_ratio")
    if bid_ask_ratio is not None:
        components.append(_clamp((bid_ask_ratio - 0.5) * 4))

    if cvd is not None:
        components.append(_clamp(cvd / 200.0))

    whale_net_flow = context.get("whale_activity", {}).get("net_flow")
    if whale_net_flow:
        components.append(_clamp(whale_net_flow / 5_000_000.0))

    liquidation_bias = context.get("liquidation_pressure", {}).get("bias")
    if liquidation_bias == "SHORT_LIQUIDATIONS":
        components.append(0.5)
    elif liquidation_bias == "LONG_LIQUIDATIONS":
        components.append(-0.5)

    fear_greed_index = context.get("fear_greed", {}).get("index")
    if fear_greed_index is not None:
        # extreme fear/greed read as contrarian, weighted down (macro signal)
        components.append(-_clamp((fear_greed_index - 50) / 50.0) * 0.6)

    news_sentiment_score = context.get("news_sentiment")
    if news_sentiment_score:
        components.append(_clamp(news_sentiment_score))

    if not components:
        return "NEUTRAL", 0.0

    bias_score = round(_clamp(sum(components) / len(components)), 4)

    if bias_score > BIAS_NEUTRAL_BAND:
        label = "BULLISH"
    elif bias_score < -BIAS_NEUTRAL_BAND:
        label = "BEARISH"
    else:
        label = "NEUTRAL"

    return label, bias_score


async def get_context(symbol: str, force_refresh: bool = False) -> dict:
    symbol = symbol.upper()
    cached = _cache.get(symbol)
    now = time.time()

    if not force_refresh and cached and now - cached["ts"] < CACHE_TTL_SECONDS:
        return cached["data"]

    context = await _collect(symbol)
    market_bias, bias_score = _compute_bias(context)
    context["market_bias"] = market_bias
    context["bias_score"] = bias_score
    context["symbol"] = symbol
    context["updated_at"] = now

    _cache[symbol] = {"data": context, "ts": now}
    return context


def confidence_adjustment(context: dict, direction: str) -> float:
    """Bullish context agreeing with a LONG (or bearish agreeing with a
    SHORT) adds +5 to +15 confidence; disagreement subtracts the same
    range. Never changes direction - only the magnitude of confidence."""
    if direction not in ("LONG", "SHORT"):
        return 0.0

    bias_score = context.get("bias_score", 0.0) or 0.0
    if bias_score == 0.0:
        return 0.0

    agrees = (direction == "LONG" and bias_score > 0) or (direction == "SHORT" and bias_score < 0)
    magnitude = round(5 + 10 * abs(bias_score), 2)

    return magnitude if agrees else -magnitude
