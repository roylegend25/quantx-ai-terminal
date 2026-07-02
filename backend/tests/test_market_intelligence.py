import asyncio

from app.intelligence import (
    fear_greed,
    funding,
    liquidations,
    market_intelligence,
    news_sentiment,
    open_interest,
    orderflow,
    whale_tracker,
)


def _patch_collectors(monkeypatch, *, funding_rate, oi_change_pct, cvd, long_short_ratio,
                       bid_ask_ratio, whale_net_flow, liquidation_bias, fear_greed_index):
    async def fake_funding(symbol):
        return {"funding_rate": funding_rate, "mark_price": 100.0, "index_price": 100.0, "next_funding_time": 0}

    async def fake_oi(symbol, period="5m"):
        return {"open_interest": 1000.0, "oi_change_pct": oi_change_pct}

    async def fake_flow(symbol):
        return {
            "long_short_ratio": long_short_ratio, "buy_sell_ratio": 1.0,
            "buy_volume": 100.0, "sell_volume": 100.0,
            "bid_ask_ratio": bid_ask_ratio, "cvd": cvd,
        }

    async def fake_whale(symbol, notional_threshold=100_000.0, limit=1000):
        bias = "BUY" if whale_net_flow > 0 else ("SELL" if whale_net_flow < 0 else "NEUTRAL")
        return {"whale_trade_count": 5, "whale_net_flow": whale_net_flow, "whale_bias": bias}

    async def fake_liquidations(symbol, listen_seconds=2.0):
        return {"liquidation_count": 1, "liquidation_notional": 10000.0, "liquidation_pressure": liquidation_bias}

    async def fake_fear_greed():
        return {"fear_greed_index": fear_greed_index, "fear_greed_label": "Neutral"}

    async def fake_news():
        return {"news_sentiment": 0.0, "status": "unavailable"}

    monkeypatch.setattr(funding, "fetch", fake_funding)
    monkeypatch.setattr(open_interest, "fetch", fake_oi)
    monkeypatch.setattr(orderflow, "fetch", fake_flow)
    monkeypatch.setattr(whale_tracker, "fetch", fake_whale)
    monkeypatch.setattr(liquidations, "fetch", fake_liquidations)
    monkeypatch.setattr(fear_greed, "fetch", fake_fear_greed)
    monkeypatch.setattr(news_sentiment, "fetch", fake_news)


def test_context_shape_matches_spec(monkeypatch):
    _patch_collectors(
        monkeypatch, funding_rate=0.0, oi_change_pct=0.0, cvd=0.0, long_short_ratio=1.0,
        bid_ask_ratio=0.5, whale_net_flow=0.0, liquidation_bias="NEUTRAL", fear_greed_index=50,
    )
    context = asyncio.run(market_intelligence.get_context("BTCUSDT", force_refresh=True))

    for key in [
        "funding_rate", "open_interest", "oi_change_pct", "cvd", "bid_ask_ratio",
        "whale_activity", "liquidation_pressure", "fear_greed", "market_bias",
    ]:
        assert key in context


def test_strongly_bullish_signals_produce_bullish_bias(monkeypatch):
    _patch_collectors(
        monkeypatch,
        funding_rate=0.0006, oi_change_pct=4.0, cvd=500.0, long_short_ratio=0.5,
        bid_ask_ratio=0.8, whale_net_flow=8_000_000.0, liquidation_bias="SHORT_LIQUIDATIONS",
        fear_greed_index=10,
    )
    context = asyncio.run(market_intelligence.get_context("BTCUSDT", force_refresh=True))

    assert context["market_bias"] == "BULLISH"
    assert context["bias_score"] > 0.15


def test_strongly_bearish_signals_produce_bearish_bias(monkeypatch):
    _patch_collectors(
        monkeypatch,
        funding_rate=-0.0006, oi_change_pct=4.0, cvd=-500.0, long_short_ratio=1.5,
        bid_ask_ratio=0.2, whale_net_flow=-8_000_000.0, liquidation_bias="LONG_LIQUIDATIONS",
        fear_greed_index=90,
    )
    context = asyncio.run(market_intelligence.get_context("BTCUSDT", force_refresh=True))

    assert context["market_bias"] == "BEARISH"
    assert context["bias_score"] < -0.15


def test_context_is_cached_within_ttl(monkeypatch):
    calls = {"count": 0}

    _patch_collectors(
        monkeypatch, funding_rate=0.0, oi_change_pct=0.0, cvd=0.0, long_short_ratio=1.0,
        bid_ask_ratio=0.5, whale_net_flow=0.0, liquidation_bias="NEUTRAL", fear_greed_index=50,
    )

    async def counting_funding(symbol):
        calls["count"] += 1
        return {"funding_rate": 0.0, "mark_price": 100.0, "index_price": 100.0, "next_funding_time": 0}

    # override just funding.fetch (after _patch_collectors) so we can count calls
    monkeypatch.setattr(funding, "fetch", counting_funding)

    asyncio.run(market_intelligence.get_context("ETHUSDT", force_refresh=True))
    asyncio.run(market_intelligence.get_context("ETHUSDT"))

    assert calls["count"] == 1


def test_confidence_adjustment_agreement_boosts_and_disagreement_penalizes():
    bullish_context = {"bias_score": 0.8}
    assert 5 <= market_intelligence.confidence_adjustment(bullish_context, "LONG") <= 15
    assert -15 <= market_intelligence.confidence_adjustment(bullish_context, "SHORT") <= -5


def test_confidence_adjustment_neutral_and_no_trade_are_zero():
    assert market_intelligence.confidence_adjustment({"bias_score": 0.0}, "LONG") == 0.0
    assert market_intelligence.confidence_adjustment({"bias_score": 0.9}, "NO_TRADE") == 0.0
