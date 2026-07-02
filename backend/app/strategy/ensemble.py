from app.strategy.manager import StrategyManager
from app.strategy.regime import detect
from app.strategy.performance_repository import repository as performance_repository

manager = StrategyManager()

NO_TRADE_BAND = 2.0


def _probability_up(result: dict) -> float:
    confidence = max(0.0, min(100.0, result.get("confidence") or 0))
    direction = result.get("direction")

    if direction == "LONG":
        return 50 + confidence / 2
    if direction == "SHORT":
        return 50 - confidence / 2
    return 50.0


def evaluate(features: dict):

    strategies = manager.evaluate(features)

    regime = detect(features)

    weights = performance_repository.get_weights()

    probability_up = 0.0
    for name, result in strategies.items():
        probability_up += _probability_up(result) * weights.get(name, 0.0)

    probability_up = max(0.0, min(100.0, probability_up))
    probability_down = 100 - probability_up

    confidence = round(abs(probability_up - 50) * 2, 1)

    if probability_up - 50 > NO_TRADE_BAND:
        direction = "LONG"
    elif 50 - probability_up > NO_TRADE_BAND:
        direction = "SHORT"
    else:
        direction = "NO_TRADE"

    return {
        "regime": regime,
        "strategies": strategies,
        "weights": weights,
        "ensemble": {
            "direction": direction,
            "confidence": confidence,
            "probability_up": round(probability_up),
            "probability_down": round(probability_down),
        },
    }
