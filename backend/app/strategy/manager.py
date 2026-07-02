from app.strategy import (
    trend,
    momentum,
    mean_reversion,
)

STRATEGIES = {
    "trend": trend.evaluate,
    "momentum": momentum.evaluate,
    "mean_reversion": mean_reversion.evaluate,
}

class StrategyManager:

    def evaluate(self, features: dict):

        results = {}

        for name, strategy in STRATEGIES.items():
            try:
                results[name] = strategy(features)
            except Exception as e:
                results[name] = {
                    "direction": "NO_TRADE",
                    "confidence": 0,
                    "reason": str(e),
                }

        return results
