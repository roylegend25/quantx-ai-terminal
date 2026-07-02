from app.strategy.manager import StrategyManager
from app.strategy.regime import detect

manager = StrategyManager()

def evaluate(features: dict):

    strategies = manager.evaluate(features)

    regime = detect(features)

    weights = {
        "TRENDING": {
            "trend": 0.50,
            "momentum": 0.35,
            "mean_reversion": 0.15,
        },
        "RANGING": {
            "trend": 0.20,
            "momentum": 0.20,
            "mean_reversion": 0.60,
        },
        "HIGH_VOL": {
            "trend": 0.40,
            "momentum": 0.40,
            "mean_reversion": 0.20,
        },
        "NORMAL": {
            "trend": 0.34,
            "momentum": 0.33,
            "mean_reversion": 0.33,
        },
    }

    w = weights.get(regime, weights["NORMAL"])

    long_score = 0.0
    short_score = 0.0

    for name, result in strategies.items():

        weight = w.get(name, 0)

        if result["direction"] == "LONG":
            long_score += result["confidence"] * weight

        elif result["direction"] == "SHORT":
            short_score += result["confidence"] * weight

    total = long_score + short_score

    if total == 0:
        direction = "NO_TRADE"
        confidence = 0
        probability_up = 50
        probability_down = 50

    elif long_score > short_score:
        direction = "LONG"
        confidence = round(long_score, 1)
        probability_up = round(long_score / total * 100)
        probability_down = 100 - probability_up

    else:
        direction = "SHORT"
        confidence = round(short_score, 1)
        probability_down = round(short_score / total * 100)
        probability_up = 100 - probability_down

    return {
        "regime": regime,
        "strategies": strategies,
        "ensemble": {
            "direction": direction,
            "confidence": confidence,
            "probability_up": probability_up,
            "probability_down": probability_down,
        },
    }
