from app.intelligence import liquidation_heatmap


def _synthetic_klines(n: int = 192, price: float = 100.0) -> list[list]:
    """Flat-ish synthetic 15m candles so the ATR/volume-profile math has
    something realistic to chew on without hitting the network."""
    klines = []
    for i in range(n):
        open_ = price + (i % 5) * 0.05
        high = open_ + 0.4
        low = open_ - 0.4
        close = open_ + 0.1
        quote_volume = 10_000.0 + (i % 10) * 500.0
        klines.append([i * 900_000, open_, high, low, close, 100.0, i * 900_000 + 900_000, quote_volume, 50, 50.0, 5000.0, "0"])
    return klines


def _depth(price: float = 100.0) -> dict:
    return {
        "bids": [[str(round(price - 0.1 * i, 2)), "5.0"] for i in range(1, 20)],
        "asks": [[str(round(price + 0.1 * i, 2)), "5.0"] for i in range(1, 20)],
    }


def test_compute_returns_full_shape_and_grounds_notional_in_open_interest():
    klines = _synthetic_klines()
    result = liquidation_heatmap.compute(
        symbol="BTCUSDT",
        klines=klines,
        depth=_depth(),
        oi_data={"open_interest": 1000.0, "oi_change_pct": 0.0},
        funding_data={"funding_rate": 0.0, "mark_price": 100.0, "index_price": 100.0},
        liquidation_data={"liquidation_count": 0, "liquidation_notional": 0.0, "liquidation_pressure": "NEUTRAL"},
    )

    for key in [
        "symbol", "current_price", "atr", "clusters", "largest_long_cluster", "largest_short_cluster",
        "largest_cluster", "liquidity_imbalance_pct", "magnet_price", "stop_hunt_zone", "risk_score",
        "open_interest_notional", "recent_liquidations", "data_source",
    ]:
        assert key in result

    assert len(result["clusters"]) == liquidation_heatmap.NUM_BUCKETS
    assert result["data_source"] == "binance_estimated"
    # every cluster's $ value must be traceable back to the real OI notional, not an arbitrary unit
    assert result["open_interest_notional"] == 1000.0 * 100.0

    total_notional = sum(c["long_notional"] + c["short_notional"] for c in result["clusters"])
    assert total_notional > 0
    assert total_notional <= result["open_interest_notional"] * 1.5  # OI + a small order-book/liquidation top-up


def test_positive_funding_skews_more_open_interest_to_the_long_side():
    klines = _synthetic_klines()
    common = dict(
        symbol="BTCUSDT",
        klines=klines,
        depth=_depth(),
        oi_data={"open_interest": 1000.0, "oi_change_pct": 0.0},
        liquidation_data={"liquidation_count": 0, "liquidation_notional": 0.0, "liquidation_pressure": "NEUTRAL"},
    )
    bullish_funding = liquidation_heatmap.compute(
        funding_data={"funding_rate": 0.0015, "mark_price": 100.0, "index_price": 100.0}, **common
    )
    bearish_funding = liquidation_heatmap.compute(
        funding_data={"funding_rate": -0.0015, "mark_price": 100.0, "index_price": 100.0}, **common
    )

    assert bullish_funding["liquidity_imbalance_pct"] > bearish_funding["liquidity_imbalance_pct"]


def test_observed_liquidations_boost_the_current_price_bucket():
    klines = _synthetic_klines()
    quiet = liquidation_heatmap.compute(
        symbol="BTCUSDT",
        klines=klines,
        depth=_depth(),
        oi_data={"open_interest": 1000.0, "oi_change_pct": 0.0},
        funding_data={"funding_rate": 0.0, "mark_price": 100.0, "index_price": 100.0},
        liquidation_data={"liquidation_count": 0, "liquidation_notional": 0.0, "liquidation_pressure": "NEUTRAL"},
    )
    with_liquidations = liquidation_heatmap.compute(
        symbol="BTCUSDT",
        klines=klines,
        depth=_depth(),
        oi_data={"open_interest": 1000.0, "oi_change_pct": 0.0},
        funding_data={"funding_rate": 0.0, "mark_price": 100.0, "index_price": 100.0},
        liquidation_data={"liquidation_count": 3, "liquidation_notional": 50_000.0, "liquidation_pressure": "LONG_LIQUIDATIONS"},
    )

    assert with_liquidations["recent_liquidations"]["notional"] == 50_000.0
    quiet_total = sum(c["long_notional"] + c["short_notional"] for c in quiet["clusters"])
    boosted_total = sum(c["long_notional"] + c["short_notional"] for c in with_liquidations["clusters"])
    assert boosted_total > quiet_total


def test_degraded_response_is_structurally_complete_when_upstream_fails():
    result = liquidation_heatmap.degraded("BTCUSDT", error="network unavailable")
    assert result["data_source"] == "unavailable"
    assert result["clusters"] == []
    assert result["current_price"] is None
    assert result["error"] == "network unavailable"
