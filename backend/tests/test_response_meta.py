import time

from app.core.response_meta import market_meta


def test_market_meta_defaults_are_honest_not_fabricated():
    before = time.time()
    meta = market_meta(source="binance_futures", source_type="exchange_rest")
    after = time.time()
    assert meta["source"] == "binance_futures"
    assert meta["source_type"] == "exchange_rest"
    assert meta["market_timestamp"] is None
    assert meta["stale"] is False
    assert meta["error"] is None
    assert meta["fallback_source"] is None
    assert before <= meta["fetched_at"] <= after


def test_market_meta_fills_provided_fields_and_rounds_latency():
    meta = market_meta(
        source="hyperliquid_ws", source_type="exchange_ws",
        market_timestamp=1700000000.0, latency_ms=123.456, stale=True,
        error="handshake failed", fallback_source="binance_estimated",
    )
    assert meta["market_timestamp"] == 1700000000.0
    assert meta["latency_ms"] == 123.5
    assert meta["stale"] is True
    assert meta["error"] == "handshake failed"
    assert meta["fallback_source"] == "binance_estimated"


def test_market_meta_is_purely_additive_when_merged():
    payload = {"symbol": "BTCUSDT", "bids": [], "asks": []}
    merged = {**payload, **market_meta(source="binance_futures", source_type="exchange_rest")}
    assert merged["symbol"] == "BTCUSDT"
    assert merged["bids"] == []
    assert "source" in merged and "fetched_at" in merged
