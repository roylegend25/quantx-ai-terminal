import time

from app.strategy import (
    trend,
    momentum,
    mean_reversion,
    breakout,
)
from app.monitoring.metrics import STRATEGY_EXECUTION_TIME

STRATEGIES = {
    "trend": trend.evaluate,
    "momentum": momentum.evaluate,
    "mean_reversion": mean_reversion.evaluate,
    "breakout": breakout.evaluate,
}

class StrategyManager:

    def evaluate(self, features: dict):

        results = {}

        for name, strategy in STRATEGIES.items():
            start = time.perf_counter()
            try:
                results[name] = strategy(features)
            except Exception as e:
                results[name] = {
                    "direction": "NO_TRADE",
                    "confidence": 0,
                    "reason": str(e),
                }
            finally:
                STRATEGY_EXECUTION_TIME.labels(strategy=name).observe(time.perf_counter() - start)

        return results
