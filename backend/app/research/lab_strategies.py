"""Per-bar signal generation for the seven strategies the Research Lab
(Phase 16) compares - Trend/Momentum/MeanReversion/Breakout replay the exact
live strategy modules unchanged (app/strategy/trend.py, momentum.py,
mean_reversion.py, breakout.py), Adaptive Ensemble replays the same
NO_TRADE-band voting app/research/walk_forward.py already uses, and
Champion/Challenger ML replay whichever model currently holds that status in
the Phase 15 lifecycle registry (app/mlops/model_registry.py). Nothing here
is imported by the live prediction path - it only reads historical OHLC
candles already downloaded via /api/backtest/download.
"""

import pandas as pd

from app.mlops import model_loader
from app.mlops.model_registry import registry as mlops_registry
from app.strategy import breakout, mean_reversion, momentum, trend
from app.strategy.manager import StrategyManager

STRATEGIES = ["trend", "momentum", "mean_reversion", "breakout", "ensemble", "champion_ml", "challenger_ml"]

_SIMPLE_STRATEGIES = {
    "trend": trend.evaluate,
    "momentum": momentum.evaluate,
    "mean_reversion": mean_reversion.evaluate,
    "breakout": breakout.evaluate,
}

NO_TRADE_BAND_VOTES = 50


class ModelNotAvailableError(RuntimeError):
    """Raised when a champion_ml/challenger_ml backtest is requested but no
    MLOps-registry model with that status has a usable artifact yet."""


def _row_features(row) -> dict:
    return {
        "price": row["close"],
        "ema20": row.get("ema20", row["close"]),
        "ema50": row.get("ema50", row["close"]),
        "ema200": row.get("ema200", row["close"]),
        "rsi": row.get("rsi", 50),
        "macd_hist": row.get("macd_hist", 0),
        "bb_width": row.get("bb_width", 0.01),
        "atr": row.get("atr", 100),
    }


def _simple_strategy_signals(name: str, df: pd.DataFrame) -> list[tuple[str, float]]:
    evaluate = _SIMPLE_STRATEGIES[name]
    signals = []
    for _, row in df.iterrows():
        result = evaluate(_row_features(row))
        signals.append((result["direction"], float(result["confidence"] or 0.0)))
    return signals


def _ensemble_signals(df: pd.DataFrame) -> list[tuple[str, float]]:
    manager = StrategyManager()
    signals = []
    for _, row in df.iterrows():
        results = manager.evaluate(_row_features(row))
        long_votes = sum(r["confidence"] for r in results.values() if r["direction"] == "LONG")
        short_votes = sum(r["confidence"] for r in results.values() if r["direction"] == "SHORT")

        if long_votes > short_votes and long_votes >= NO_TRADE_BAND_VOTES:
            signals.append(("LONG", min(long_votes, 100.0)))
        elif short_votes > long_votes and short_votes >= NO_TRADE_BAND_VOTES:
            signals.append(("SHORT", min(short_votes, 100.0)))
        else:
            signals.append(("NO_TRADE", 0.0))
    return signals


def _ml_model_row(status: str, db=None) -> dict | None:
    if status == "champion":
        return mlops_registry.get_champion(db=db)
    challengers = mlops_registry.get_challengers(db=db)
    return challengers[0] if challengers else None


def _ml_signals(status: str, df: pd.DataFrame, db=None) -> list[tuple[str, float]]:
    model_row = _ml_model_row(status, db=db)
    if model_row is None or not model_row.get("model_path"):
        raise ModelNotAvailableError(
            f"No {status.capitalize()} model with a trained artifact is registered yet - "
            f"train and promote one from the AI Model Center first."
        )
    if not model_loader.is_available(model_row["model_path"]):
        raise ModelNotAvailableError(f"Model artifact for '{model_row['model_id']}' is missing on disk.")

    bundle = model_loader.load(model_row["model_path"])
    model, feature_names = bundle["model"], bundle["feature_names"]

    signals = []
    for _, row in df.iterrows():
        # historical OHLC candles only carry a subset of a trained model's
        # feature_names (no realized_volatility/trend_score/regime columns);
        # anything missing defaults to 0.0 rather than failing the backtest
        vector = [[float(row.get(name) or 0.0) for name in feature_names]]
        probability_up = float(model.predict_proba(vector)[0][1])
        confidence = round(abs(probability_up - 0.5) * 200, 2)
        direction = "LONG" if probability_up >= 0.5 else "SHORT"
        signals.append((direction, confidence))
    return signals


def generate_signals(strategy: str, df: pd.DataFrame, db=None) -> list[tuple[str, float]]:
    """Returns one (direction, confidence) tuple per row of `df`, aligned by
    position - feed straight into app/research/execution_sim.simulate_trades."""
    if strategy in _SIMPLE_STRATEGIES:
        return _simple_strategy_signals(strategy, df)
    if strategy == "ensemble":
        return _ensemble_signals(df)
    if strategy == "champion_ml":
        return _ml_signals("champion", df, db=db)
    if strategy == "challenger_ml":
        return _ml_signals("challenger", df, db=db)
    raise ValueError(f"Unknown strategy '{strategy}', expected one of {STRATEGIES}")
