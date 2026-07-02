"""Compares the live adaptive ensemble against simple reference strategies
and the ML champion's tracked record, over the same historical candles. A
research/analysis tool only - it never feeds back into live trading.
"""

from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.backtest.indicators import add_indicators
from app.ml.model_registry import registry as ml_registry
from app.research.metrics import compute_metrics
from app.research.walk_forward import simulate_ensemble


def _row_time(row):
    time = row.get("time")
    return pd.to_datetime(time, unit="ms", utc=True) if time is not None else None


def _buy_and_hold_trades(df: pd.DataFrame, starting_balance: float) -> list[dict]:
    if df.empty:
        return []

    first, last = df.iloc[0], df.iloc[-1]
    qty = starting_balance / float(first["close"])
    pnl = qty * (float(last["close"]) - float(first["close"]))

    return [
        {
            "pnl": pnl,
            "entry_time": _row_time(first),
            "exit_time": _row_time(last),
            "confidence": None,
            "correct": pnl >= 0,
        }
    ]


def _ema_cross_trades(df: pd.DataFrame) -> list[dict]:
    trades = []
    position = None
    entry_price = entry_time = None
    prev_diff = None

    for _, row in df.iterrows():
        ema20, ema50 = row.get("ema20"), row.get("ema50")
        if ema20 is None or ema50 is None:
            continue

        diff = ema20 - ema50
        price, time = float(row["close"]), _row_time(row)

        if prev_diff is not None:
            crossed_up = prev_diff <= 0 < diff
            crossed_down = prev_diff >= 0 > diff

            if position is None:
                if crossed_up:
                    position, entry_price, entry_time = "LONG", price, time
                elif crossed_down:
                    position, entry_price, entry_time = "SHORT", price, time
            elif (position == "LONG" and crossed_down) or (position == "SHORT" and crossed_up):
                pnl = (price - entry_price) if position == "LONG" else (entry_price - price)
                trades.append(
                    {"pnl": pnl, "entry_time": entry_time, "exit_time": time, "confidence": None, "correct": pnl >= 0}
                )
                position = "SHORT" if position == "LONG" else "LONG"
                entry_price, entry_time = price, time

        prev_diff = diff

    return trades


def _rsi_strategy_trades(df: pd.DataFrame) -> list[dict]:
    trades = []
    position = None
    entry_price = entry_time = None

    for _, row in df.iterrows():
        rsi = row.get("rsi")
        if rsi is None:
            continue

        price, time = float(row["close"]), _row_time(row)

        if position is None:
            if rsi < 30:
                position, entry_price, entry_time = "LONG", price, time
            elif rsi > 70:
                position, entry_price, entry_time = "SHORT", price, time
        elif position == "LONG" and rsi >= 50:
            pnl = price - entry_price
            trades.append(
                {"pnl": pnl, "entry_time": entry_time, "exit_time": time, "confidence": None, "correct": pnl >= 0}
            )
            position = None
        elif position == "SHORT" and rsi <= 50:
            pnl = entry_price - price
            trades.append(
                {"pnl": pnl, "entry_time": entry_time, "exit_time": time, "confidence": None, "correct": pnl >= 0}
            )
            position = None

    return trades


def run(symbol: str = "BTCUSDT", interval: str = "5m", starting_balance: float = 10000.0, db: Session | None = None) -> dict:
    csv_file = Path(f"/app/data/history/{symbol.upper()}/{interval}.csv")
    if not csv_file.exists():
        raise FileNotFoundError(f"{csv_file} not found - call /api/backtest/download first")

    df = add_indicators(pd.read_csv(csv_file))

    results = [
        {"name": "Buy & Hold BTC", "metrics": compute_metrics(_buy_and_hold_trades(df, starting_balance), starting_balance)},
        {"name": "EMA Cross", "metrics": compute_metrics(_ema_cross_trades(df), starting_balance)},
        {"name": "RSI Strategy", "metrics": compute_metrics(_rsi_strategy_trades(df), starting_balance)},
        {"name": "Current Adaptive Ensemble", "metrics": compute_metrics(simulate_ensemble(df), starting_balance)},
    ]

    champion = ml_registry.get_champion(db=db)
    results.append(
        {
            "name": "Current Champion ML",
            "metrics": None,
            "ml_registry_metrics": champion,
            "notes": (
                "Champion ML metrics come from the model registry's classification "
                "backtest (accuracy/precision/recall/f1/auc) against real closed-trade "
                "outcomes, not a price-history replay - see app/ml/backtest_models.py."
                if champion
                else "No champion backtest recorded yet - run `python -m app.ml.backtest_models` first."
            ),
        }
    )

    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "starting_balance": round(starting_balance, 2),
        "results": results,
    }
