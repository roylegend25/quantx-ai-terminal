"""Candidate weight calculator built on the shadow StrategyRollingMetrics data.

This is independent of StrategyPerformance.current_weight (the value
ensemble.py actually consumes today). It reads from and writes to
StrategyRollingMetrics only - nothing here affects live predictions.
"""

from sqlalchemy.orm import Session

from app.strategy.performance_repository import STRATEGY_NAMES
from app.strategy.rolling_metrics_repository import repository as rolling_metrics_repository

MIN_SCORE = 0.0001
COMPONENT_CAP = 3.0


def _clamp01(value: float, cap: float = COMPONENT_CAP) -> float:
    return max(0.0, min(value, cap)) / cap


def _regime_performance_component(regime_performance: dict) -> float:
    if not regime_performance:
        return 0.0
    win_rates = [bucket["win_rate"] for bucket in regime_performance.values()]
    return (sum(win_rates) / len(win_rates)) / 100.0


def score(stats: dict) -> float:
    """score = 0.35*win_rate + 0.20*profit_factor + 0.15*sharpe
    + 0.15*average_r + 0.10*confidence + 0.05*regime_performance
    Each component is normalized to [0, 1] before weighting."""

    win_rate = max(0.0, min(stats.get("rolling_win_rate", 0.0), 100.0)) / 100.0
    profit_factor = _clamp01(stats.get("profit_factor", 0.0))
    sharpe = _clamp01(stats.get("sharpe_ratio", 0.0))
    average_r = _clamp01(stats.get("average_r_multiple", 0.0))
    confidence = max(0.0, min(stats.get("average_confidence", 0.0), 100.0)) / 100.0
    regime_performance = _regime_performance_component(stats.get("regime_performance", {}))

    raw_score = (
        0.35 * win_rate
        + 0.20 * profit_factor
        + 0.15 * sharpe
        + 0.15 * average_r
        + 0.10 * confidence
        + 0.05 * regime_performance
    )

    return max(raw_score, MIN_SCORE)


def calculate_weights(all_stats: dict) -> dict:
    """Score every strategy and normalize so the weights sum to 1."""
    scores = {name: score(stats) for name, stats in all_stats.items()}
    total = sum(scores.values()) or 1.0
    return {name: round(s / total, 6) for name, s in scores.items()}


def recompute_and_store(db: Session | None = None) -> dict:
    """Recompute every strategy's candidate weight from its current shadow
    rolling metrics and persist it to StrategyRollingMetrics.current_weight."""
    all_stats = rolling_metrics_repository.get_all(db=db)
    weights = calculate_weights(all_stats)

    for name in STRATEGY_NAMES:
        rolling_metrics_repository.save(name, db=db, current_weight=weights[name])

    return weights
