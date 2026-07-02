from app.strategy.performance_repository import STRATEGY_NAMES, repository


def test_seed_defaults_creates_four_rows_at_equal_weight():
    repository.seed_defaults()
    all_stats = repository.get_all()

    assert set(all_stats) == set(STRATEGY_NAMES)
    for name in ["trend", "momentum", "mean_reversion", "breakout"]:
        assert all_stats[name]["current_weight"] == 0.25
    assert abs(sum(s["current_weight"] for s in all_stats.values()) - 1.0) < 1e-6


def test_get_unknown_strategy_returns_none():
    assert repository.get("not_a_real_strategy") is None


def test_get_known_strategy_auto_creates_default_row():
    stats = repository.get("trend")
    assert stats["strategy_name"] == "trend"
    assert stats["current_weight"] == 0.25
    assert stats["trades"] == 0


def test_save_upserts_arbitrary_fields():
    repository.save("trend", current_weight=0.5)
    assert repository.get("trend")["current_weight"] == 0.5


def test_weights_always_normalize_to_one():
    repository.update_metrics(
        "trend", r_multiple=2.0, win=True, confidence=80, regime="TRENDING"
    )
    repository.update_metrics(
        "momentum", r_multiple=-1.0, win=False, confidence=60, regime="TRENDING"
    )
    weights = repository.get_weights()
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_better_performing_strategy_gets_higher_weight():
    for _ in range(10):
        repository.update_metrics(
            "trend", r_multiple=2.0, win=True, confidence=90, regime="TRENDING"
        )
    for _ in range(10):
        repository.update_metrics(
            "momentum", r_multiple=-1.0, win=False, confidence=40, regime="TRENDING"
        )

    weights = repository.get_weights()
    assert weights["trend"] > weights["momentum"]
    # untouched strategies fall back to the same neutral breakeven baseline
    assert weights["mean_reversion"] == weights["breakout"]


def test_rolling_window_caps_at_20_trades():
    for _ in range(25):
        repository.update_metrics(
            "trend", r_multiple=1.0, win=True, confidence=50, regime="NORMAL"
        )
    stats = repository.get("trend")
    assert stats["trades"] == 20


def test_metrics_fields_present_and_correct():
    repository.update_metrics(
        "breakout", r_multiple=2.0, win=True, confidence=80, regime="HIGH_VOL"
    )
    repository.update_metrics(
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
    assert "current_weight" in stats
    assert "updated_at" in stats


def test_persists_across_fresh_reads():
    repository.update_metrics(
        "mean_reversion", r_multiple=1.5, win=True, confidence=75, regime="RANGING"
    )
    # simulate a fresh process reading straight from SQLite
    stats = repository.get("mean_reversion")
    assert stats["trades"] == 1
    assert stats["average_r_multiple"] == 1.5


def test_no_losses_caps_profit_factor():
    for _ in range(3):
        repository.update_metrics(
            "trend", r_multiple=1.0, win=True, confidence=80, regime="NORMAL"
        )
    stats = repository.get("trend")
    assert stats["profit_factor"] == 99.0
