from app.strategy.rolling_metrics_repository import repository


def test_get_unknown_strategy_returns_none():
    assert repository.get("not_a_real_strategy") is None


def test_get_known_strategy_auto_creates_default_row():
    stats = repository.get("trend")
    assert stats["strategy_name"] == "trend"
    assert stats["trades"] == 0
    assert stats["wins"] == 0
    assert stats["losses"] == 0


def test_save_upserts_arbitrary_fields():
    repository.save("trend", max_drawdown=1.23)
    assert repository.get("trend")["max_drawdown"] == 1.23


def test_rolling_window_caps_at_20_trades():
    for _ in range(25):
        repository.record_trade(
            "trend", r_multiple=1.0, win=True, confidence=50, regime="NORMAL"
        )
    stats = repository.get("trend")
    assert stats["trades"] == 20


def test_metrics_fields_present_and_correct():
    repository.record_trade(
        "breakout", r_multiple=2.0, win=True, confidence=80, regime="HIGH_VOL"
    )
    repository.record_trade(
        "breakout", r_multiple=-1.0, win=False, confidence=60, regime="HIGH_VOL"
    )

    stats = repository.get("breakout")

    assert stats["trades"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["rolling_win_rate"] == 50.0
    assert stats["average_r_multiple"] == 0.5
    assert stats["profit_factor"] == 2.0
    assert stats["average_confidence"] == 70.0
    assert stats["regime_performance"]["HIGH_VOL"]["trades"] == 2
    assert stats["regime_performance"]["HIGH_VOL"]["wins"] == 1
    assert "sharpe_ratio" in stats
    assert "max_drawdown" in stats
    assert "updated_at" in stats


def test_persists_across_fresh_reads():
    repository.record_trade(
        "mean_reversion", r_multiple=1.5, win=True, confidence=75, regime="RANGING"
    )
    stats = repository.get("mean_reversion")
    assert stats["trades"] == 1
    assert stats["average_r_multiple"] == 1.5


def test_shadow_current_weight_defaults_and_is_not_production():
    # the shadow table's current_weight is the Weight Calculator's candidate
    # output (tests/test_weight_calculator.py) - it is never read by
    # ensemble.py, unlike StrategyPerformance.current_weight
    stats = repository.get("trend")
    assert stats["current_weight"] == 0.25
