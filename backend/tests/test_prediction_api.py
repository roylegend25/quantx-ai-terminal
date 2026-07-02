from app.api.prediction import make_prediction
from app.strategy.performance_repository import repository as performance_repository

FEATURES = {
    "price": 100.0,
    "ema20": 95.0,
    "ema50": 90.0,
    "ema200": 85.0,
    "rsi": 65.0,
    "macd_hist": 1.0,
    "atr": 2.0,
    "bb_width": 0.03,
    "realized_volatility": 0.01,
    "trend_score": 3,
    "regime": "bullish_trend",
}


def test_prediction_exposes_strategy_weights():
    result = make_prediction(FEATURES)

    assert "strategy_weights" in result
    assert set(result["strategy_weights"].keys()) == {
        "trend", "momentum", "mean_reversion", "breakout",
    }
    assert abs(sum(result["strategy_weights"].values()) - 1.0) < 1e-6


def test_prediction_strategy_weights_match_production_repository():
    performance_repository.update_metrics(
        "trend", r_multiple=3.0, win=True, confidence=90, regime="TRENDING"
    )

    result = make_prediction(FEATURES)

    assert result["strategy_weights"] == performance_repository.get_weights()
