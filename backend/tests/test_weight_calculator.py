from app.strategy import weight_calculator
from app.strategy.performance_repository import DEFAULT_WEIGHT
from app.strategy.performance_repository import repository as performance_repository
from app.strategy.rolling_metrics_repository import repository as rolling_metrics_repository


def test_score_formula_matches_spec():
    stats = {
        "rolling_win_rate": 80.0,
        "profit_factor": 3.0,
        "sharpe_ratio": 1.5,
        "average_r_multiple": 1.5,
        "average_confidence": 90.0,
        "regime_performance": {"TRENDING": {"trades": 4, "wins": 4, "win_rate": 100.0}},
    }
    expected = (
        0.35 * 0.80
        + 0.20 * (3.0 / 3.0)
        + 0.15 * (1.5 / 3.0)
        + 0.15 * (1.5 / 3.0)
        + 0.10 * 0.90
        + 0.05 * 1.0
    )
    assert abs(weight_calculator.score(stats) - expected) < 1e-9


def test_score_floors_at_min_for_all_zero_stats():
    stats = {
        "rolling_win_rate": 0.0,
        "profit_factor": 0.0,
        "sharpe_ratio": 0.0,
        "average_r_multiple": 0.0,
        "average_confidence": 0.0,
        "regime_performance": {},
    }
    assert weight_calculator.score(stats) == weight_calculator.MIN_SCORE


def test_calculate_weights_sums_to_one():
    all_stats = rolling_metrics_repository.get_all()
    weights = weight_calculator.calculate_weights(all_stats)
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    assert set(weights.keys()) == {"trend", "momentum", "mean_reversion", "breakout"}


def test_better_performing_strategy_gets_higher_weight():
    for _ in range(10):
        rolling_metrics_repository.record_trade(
            "trend", r_multiple=2.0, win=True, confidence=90, regime="TRENDING"
        )
    for _ in range(10):
        rolling_metrics_repository.record_trade(
            "momentum", r_multiple=-1.0, win=False, confidence=20, regime="TRENDING"
        )

    all_stats = rolling_metrics_repository.get_all()
    weights = weight_calculator.calculate_weights(all_stats)
    assert weights["trend"] > weights["momentum"]


def test_recompute_and_store_persists_current_weight():
    for _ in range(5):
        rolling_metrics_repository.record_trade(
            "breakout", r_multiple=3.0, win=True, confidence=95, regime="HIGH_VOL"
        )

    weights = weight_calculator.recompute_and_store()

    stored = rolling_metrics_repository.get_all()
    for name, w in weights.items():
        assert stored[name]["current_weight"] == w
    # 6-decimal rounding on 4 independently-rounded shares can leave a few
    # ulp of slack in the sum
    assert abs(sum(s["current_weight"] for s in stored.values()) - 1.0) < 1e-4


def test_recompute_and_store_never_touches_production_weight():
    for _ in range(8):
        rolling_metrics_repository.record_trade(
            "mean_reversion", r_multiple=4.0, win=True, confidence=99, regime="RANGING"
        )
    weight_calculator.recompute_and_store()

    # the shadow candidate weight moved...
    assert rolling_metrics_repository.get("mean_reversion")["current_weight"] > DEFAULT_WEIGHT

    # ...but StrategyPerformance.current_weight (what ensemble.py reads) did not
    assert performance_repository.get_weights()["mean_reversion"] == DEFAULT_WEIGHT
