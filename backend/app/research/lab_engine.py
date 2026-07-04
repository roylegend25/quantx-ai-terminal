"""Orchestrates one Research Lab backtest run (Phase 16): loads historical
candles already downloaded via /api/backtest/download, generates a
strategy's signals (app/research/lab_strategies.py), replays them through
the cost/risk execution simulator (app/research/execution_sim.py), and
scores the result (app/research/metrics.py). Ties those together for
POST /api/research/lab/run; persistence lives in app/research/lab_repository.py.
"""

from pathlib import Path

import pandas as pd

from app.backtest.indicators import add_indicators
from app.research import execution_sim, lab_strategies
from app.research.metrics import average_metrics, compute_metrics


def history_path(symbol: str, timeframe: str) -> Path:
    return Path(f"/app/data/history/{symbol.upper()}/{timeframe}.csv")


def load_history(
    symbol: str,
    timeframe: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    csv_file = history_path(symbol, timeframe)
    if not csv_file.exists():
        raise FileNotFoundError(f"{csv_file} not found - call /api/backtest/download first")

    df = add_indicators(pd.read_csv(csv_file))
    if "time" in df.columns and (start_date or end_date):
        ts = pd.to_datetime(df["time"], unit="ms", utc=True)
        mask = pd.Series(True, index=df.index)
        if start_date:
            mask &= ts >= pd.to_datetime(start_date, utc=True)
        if end_date:
            mask &= ts <= pd.to_datetime(end_date, utc=True)
        df = df[mask].reset_index(drop=True)
    return df


def drawdown_curve(equity_curve: list[float]) -> list[float]:
    if not equity_curve:
        return []
    peak = equity_curve[0]
    curve = []
    for equity in equity_curve:
        peak = max(peak, equity)
        curve.append(round((peak - equity) / peak * 100, 4) if peak > 0 else 0.0)
    return curve


def run_backtest(
    strategy: str,
    symbol: str,
    timeframe: str,
    start_date: str | None = None,
    end_date: str | None = None,
    cost: execution_sim.CostConfig | None = None,
    risk: execution_sim.RiskConfig | None = None,
    starting_balance: float = 10000.0,
    db=None,
) -> dict:
    df = load_history(symbol, timeframe, start_date, end_date)
    signals = lab_strategies.generate_signals(strategy, df, db=db)
    trades = execution_sim.simulate_trades(df, signals, cost=cost, risk=risk)
    metrics = compute_metrics(trades, starting_balance=starting_balance)

    span_start = span_end = None
    if "time" in df.columns and len(df):
        ts = pd.to_datetime(df["time"], unit="ms", utc=True)
        span_start, span_end = ts.min(), ts.max()

    return {
        "strategy": strategy,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "start_date": span_start,
        "end_date": span_end,
        "bars": len(df),
        "metrics": metrics,
        "trades": trades,
        "equity_curve": metrics["equity_curve"],
        "drawdown_curve": drawdown_curve(metrics["equity_curve"]),
    }


def run_walkforward(
    strategy: str,
    symbol: str,
    timeframe: str,
    cost: execution_sim.CostConfig | None = None,
    risk: execution_sim.RiskConfig | None = None,
    train_bars: int = 500,
    validate_bars: int = 150,
    step_bars: int | None = None,
    starting_balance: float = 10000.0,
    db=None,
) -> dict:
    """Train -> Validate -> Advance Window -> Repeat, generalized to any of
    the seven Research Lab strategies. app/research/walk_forward.py plays
    the same role but is hardcoded to the adaptive ensemble; this reuses its
    windowing approach against lab_strategies.generate_signals +
    execution_sim.simulate_trades instead so any strategy can be walked
    forward with the same cost/risk configuration it was backtested with."""
    df = load_history(symbol, timeframe)
    step = step_bars or validate_bars

    windows = []
    start = 0
    while start + train_bars + validate_bars <= len(df):
        validate_slice = df.iloc[start + train_bars : start + train_bars + validate_bars].reset_index(drop=True)

        signals = lab_strategies.generate_signals(strategy, validate_slice, db=db)
        trades = execution_sim.simulate_trades(validate_slice, signals, cost=cost, risk=risk)
        window_metrics = compute_metrics(trades, starting_balance=starting_balance)

        windows.append(
            {
                "window": len(windows) + 1,
                "validate_range": [int(start + train_bars), int(start + train_bars + validate_bars - 1)],
                "metrics": window_metrics,
            }
        )
        start += step

    if not windows:
        raise ValueError(f"Not enough candles ({len(df)}) for train_bars={train_bars} + validate_bars={validate_bars}")

    return {
        "strategy": strategy,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "windows_run": len(windows),
        "train_bars": train_bars,
        "validate_bars": validate_bars,
        "step_bars": step,
        "windows": windows,
        "average_metrics": average_metrics([w["metrics"] for w in windows]),
    }
