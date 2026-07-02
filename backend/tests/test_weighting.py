from app.strategy import weighting


def test_cold_start_weights_are_equal():
    weights = weighting.get_weights()
    assert set(weights) == set(weighting.STRATEGY_NAMES)
    for w in weights.values():
        assert w == weighting.DEFAULT_WEIGHT
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_weights_always_normalize_to_one():
    weighting.record_trade_result(
        "trend", r_multiple=2.0, win=True, confidence=80, regime="TRENDING"
    )
    weighting.record_trade_result(
        "momentum", r_multiple=-1.0, win=False, confidence=60, regime="TRENDING"
    )
    weights = weighting.get_weights()
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_better_performing_strategy_gets_higher_weight():
    for _ in range(10):
        weighting.record_trade_result(
            "trend", r_multiple=2.0, win=True, confidence=90, regime="TRENDING"
        )
    for _ in range(10):
        weighting.record_trade_result(
            "momentum", r_multiple=-1.0, win=False, confidence=40, regime="TRENDING"
        )

    weights = weighting.get_weights()
    assert weights["trend"] > weights["momentum"]
    # untouched strategies fall back to the neutral (average) score
    assert weights["mean_reversion"] == weights["breakout"]


def test_rolling_window_caps_at_20_trades():
    for i in range(25):
        weighting.record_trade_result(
            "trend", r_multiple=1.0, win=True, confidence=50, regime="NORMAL"
        )
    stats = weighting.get_stats()
    assert stats["trend"]["trade_count"] == 20


def test_stats_fields_present_and_correct():
    weighting.record_trade_result(
        "breakout", r_multiple=2.0, win=True, confidence=80, regime="HIGH_VOL"
    )
    weighting.record_trade_result(
        "breakout", r_multiple=-1.0, win=False, confidence=60, regime="HIGH_VOL"
    )

    stats = weighting.get_stats()["breakout"]

    assert stats["trade_count"] == 2
    assert stats["win_rate"] == 50.0
    assert stats["avg_r_multiple"] == 0.5
    assert stats["profit_factor"] == 2.0
    assert stats["avg_confidence"] == 70.0
    assert stats["regime_performance"]["HIGH_VOL"]["trades"] == 2
    assert stats["regime_performance"]["HIGH_VOL"]["wins"] == 1
    assert "sharpe_ratio" in stats
    assert "max_drawdown" in stats
    assert "weight" in stats
    assert "updated_at" in stats


def test_persists_across_fresh_reads():
    weighting.record_trade_result(
        "mean_reversion", r_multiple=1.5, win=True, confidence=75, regime="RANGING"
    )
    # simulate a fresh process reading straight from SQLite
    stats = weighting.get_stats()["mean_reversion"]
    assert stats["trade_count"] == 1
    assert stats["avg_r_multiple"] == 1.5


def test_no_losses_caps_profit_factor():
    for _ in range(3):
        weighting.record_trade_result(
            "trend", r_multiple=1.0, win=True, confidence=80, regime="NORMAL"
        )
    stats = weighting.get_stats()["trend"]
    assert stats["profit_factor"] == 99.0
