"""Walk-forward testing for the live adaptive ensemble.

    python -m app.research.walk_forward

Train -> Validate -> Advance Window -> Repeat. The ensemble is rule-based
(no fitted parameters), so the "train" phase is a no-op reserved for future
parameter search; each window's "validate" phase replays the same
StrategyManager the live prediction path uses (app/strategy/manager.py) over
that window's candles and scores the resulting trades. Read-only against
historical CSVs downloaded via /api/backtest/download - never touches the
live prediction path.
"""

from pathlib import Path

import pandas as pd

from app.backtest.indicators import add_indicators
from app.research.metrics import average_metrics, compute_metrics
from app.strategy.manager import StrategyManager

manager = StrategyManager()


def _row_time(row):
    time = row.get("time")
    return pd.to_datetime(time, unit="ms", utc=True) if time is not None else None


def simulate_ensemble(df: pd.DataFrame) -> list[dict]:
    """Replays the adaptive ensemble's LONG/SHORT/NO_TRADE crossover logic
    (same voting rule as app/backtest/engine.py) over one slice of candles
    and returns the resulting closed trades."""
    trades = []
    position = None
    entry_price = None
    entry_time = None
    entry_confidence = None

    for _, row in df.iterrows():
        features = {
            "price": row["close"],
            "ema20": row.get("ema20", row["close"]),
            "ema50": row.get("ema50", row["close"]),
            "ema200": row.get("ema200", row["close"]),
            "rsi": row.get("rsi", 50),
            "macd_hist": row.get("macd_hist", 0),
            "bb_width": row.get("bb_width", 0.01),
            "atr": row.get("atr", 100),
        }
        results = manager.evaluate(features)

        long_votes = sum(r["confidence"] for r in results.values() if r["direction"] == "LONG")
        short_votes = sum(r["confidence"] for r in results.values() if r["direction"] == "SHORT")

        signal = "NO_TRADE"
        if long_votes > short_votes and long_votes >= 50:
            signal = "LONG"
        elif short_votes > long_votes and short_votes >= 50:
            signal = "SHORT"

        price = float(row["close"])
        time = _row_time(row)

        if position is None:
            if signal in ("LONG", "SHORT"):
                position, entry_price, entry_time = signal, price, time
                entry_confidence = min(max(long_votes, short_votes), 100.0)
        elif signal != position and signal != "NO_TRADE":
            pnl = (price - entry_price) if position == "LONG" else (entry_price - price)
            trades.append(
                {
                    "pnl": pnl,
                    "entry_time": entry_time,
                    "exit_time": time,
                    "confidence": entry_confidence,
                    "correct": pnl >= 0,
                }
            )
            position, entry_price, entry_time = signal, price, time
            entry_confidence = min(max(long_votes, short_votes), 100.0)

    return trades


def run(
    symbol: str = "BTCUSDT",
    interval: str = "5m",
    train_bars: int = 500,
    validate_bars: int = 150,
    step_bars: int | None = None,
) -> dict:
    csv_file = Path(f"/app/data/history/{symbol.upper()}/{interval}.csv")
    if not csv_file.exists():
        raise FileNotFoundError(f"{csv_file} not found - call /api/backtest/download first")

    df = add_indicators(pd.read_csv(csv_file))
    step = step_bars or validate_bars

    windows = []
    start = 0
    while start + train_bars + validate_bars <= len(df):
        train_slice = df.iloc[start : start + train_bars]  # reserved for future parameter fitting
        validate_slice = df.iloc[start + train_bars : start + train_bars + validate_bars]

        trades = simulate_ensemble(validate_slice)
        window_metrics = compute_metrics(trades)
        windows.append(
            {
                "window": len(windows) + 1,
                "train_range": [int(train_slice.index.min()), int(train_slice.index.max())],
                "validate_range": [int(validate_slice.index.min()), int(validate_slice.index.max())],
                "metrics": window_metrics,
            }
        )
        start += step

    if not windows:
        raise ValueError(
            f"Not enough candles ({len(df)}) for train_bars={train_bars} + validate_bars={validate_bars}"
        )

    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "windows_run": len(windows),
        "train_bars": train_bars,
        "validate_bars": validate_bars,
        "step_bars": step,
        "windows": windows,
        "average_metrics": average_metrics([w["metrics"] for w in windows]),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=2, default=str))
