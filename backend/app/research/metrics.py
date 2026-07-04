"""Shared trade-level performance metrics used by every research module
(experiment_tracker, walk_forward, monte_carlo, benchmark). Independent of
the classification metrics in app/ml/evaluator.py - those score a model's
predictions, these score a sequence of realized trades.

A trade dict is expected to look like:
    {
        "pnl": float,
        "entry_time": datetime | None,
        "exit_time": datetime | None,
        "confidence": float | None,
        "correct": bool | None,   # did the predicted direction win
        "r_multiple": float | None,  # pnl / initial risk (Phase 16 execution_sim trades only)
    }
Only "pnl" is required - the rest degrade gracefully to None when absent.
"""

import math
from statistics import mean, pstdev

YEAR_SECONDS = 365.25 * 24 * 3600
MIN_CAGR_SPAN_YEARS = 7 / 365.25  # don't annualize a window shorter than a week

AVERAGEABLE_FIELDS = [
    "win_rate",
    "profit_factor",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "max_drawdown_pct",
    "cagr_pct",
    "expectancy",
    "average_holding_time_seconds",
    "average_confidence",
    "prediction_accuracy_pct",
    "total_return_pct",
    "average_r",
    "exposure_time_pct",
]


def years_between(start, end) -> float:
    if not start or not end:
        return 0.0
    delta = (end - start).total_seconds()
    return max(delta, 0.0) / YEAR_SECONDS


def _empty_metrics(starting_balance: float) -> dict:
    return {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "profit_factor": None,
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "calmar_ratio": None,
        "max_drawdown_pct": 0.0,
        "cagr_pct": None,
        "expectancy": 0.0,
        "average_holding_time_seconds": None,
        "average_confidence": None,
        "prediction_accuracy_pct": None,
        "total_return_pct": 0.0,
        "best_trade_pnl": None,
        "worst_trade_pnl": None,
        "average_r": None,
        "exposure_time_pct": None,
        "starting_balance": round(starting_balance, 2),
        "final_balance": round(starting_balance, 2),
        "equity_curve": [round(starting_balance, 2)],
    }


def compute_metrics(trades: list[dict], starting_balance: float = 10000.0) -> dict:
    if not trades:
        return _empty_metrics(starting_balance)

    trades = sorted(
        trades,
        key=lambda t: t.get("exit_time") or t.get("entry_time") or 0,
    )

    balance = starting_balance
    equity_curve = [starting_balance]
    returns = []
    wins, losses = [], []
    holding_times = []
    confidences = []
    correctness = []
    pnls = []
    r_multiples = []

    for t in trades:
        pnl = float(t.get("pnl") or 0.0)
        prior_balance = balance
        balance += pnl
        equity_curve.append(balance)
        returns.append(pnl / prior_balance if prior_balance else 0.0)
        pnls.append(pnl)

        (wins if pnl >= 0 else losses).append(pnl)

        entry_time, exit_time = t.get("entry_time"), t.get("exit_time")
        if entry_time and exit_time:
            holding_times.append((exit_time - entry_time).total_seconds())

        if t.get("confidence") is not None:
            confidences.append(float(t["confidence"]))
        if t.get("correct") is not None:
            correctness.append(1.0 if t["correct"] else 0.0)
        if t.get("r_multiple") is not None:
            r_multiples.append(float(t["r_multiple"]))

    peak = equity_curve[0]
    max_dd = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)

    gross_profit = sum(w for w in wins if w > 0)
    gross_loss = abs(sum(losses))
    win_rate = len(wins) / len(trades) * 100
    avg_win = mean(wins) if wins else 0.0
    avg_loss = mean([abs(loss) for loss in losses]) if losses else 0.0
    expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    entry_times = [t["entry_time"] for t in trades if t.get("entry_time")]
    exit_times = [t["exit_time"] for t in trades if t.get("exit_time")]
    span_start = min(entry_times) if entry_times else None
    span_end = max(exit_times) if exit_times else None
    years = years_between(span_start, span_end)

    total_return = (balance - starting_balance) / starting_balance
    # extrapolating a short window to a full year via (final/initial)**(1/years)
    # blows up to absurd magnitudes as years -> 0, so only annualize once the
    # backtest actually spans a meaningful chunk of a year
    cagr = (
        ((balance / starting_balance) ** (1 / years) - 1) * 100
        if years >= MIN_CAGR_SPAN_YEARS and balance > 0
        else None
    )

    trades_per_year = len(trades) / years if years > 0 else float(len(trades))
    mean_return = mean(returns) if returns else 0.0
    std_return = pstdev(returns) if returns else 0.0
    sharpe = (mean_return / std_return) * math.sqrt(trades_per_year) if std_return > 0 else None

    downside_returns = [r for r in returns if r < 0]
    if len(downside_returns) > 1:
        downside_std = pstdev(downside_returns)
    elif len(downside_returns) == 1:
        downside_std = abs(downside_returns[0])
    else:
        downside_std = 0.0
    sortino = (mean_return / downside_std) * math.sqrt(trades_per_year) if downside_std > 0 else None

    calmar = (cagr / max_dd) if cagr is not None and max_dd > 0 else None

    # exposure = fraction of the backtest's wall-clock span actually spent
    # in a position; only meaningful once the run spans a real time window
    span_seconds = years * YEAR_SECONDS
    exposure_time_pct = (
        round(sum(holding_times) / span_seconds * 100, 2) if holding_times and span_seconds > 0 else None
    )

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "sharpe_ratio": round(sharpe, 3) if sharpe is not None else None,
        "sortino_ratio": round(sortino, 3) if sortino is not None else None,
        "calmar_ratio": round(calmar, 3) if calmar is not None else None,
        "max_drawdown_pct": round(max_dd, 2),
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "expectancy": round(expectancy, 4),
        "average_holding_time_seconds": round(mean(holding_times), 1) if holding_times else None,
        "average_confidence": round(mean(confidences), 2) if confidences else None,
        "prediction_accuracy_pct": round(mean(correctness) * 100, 2) if correctness else None,
        "total_return_pct": round(total_return * 100, 2),
        "best_trade_pnl": round(max(pnls), 2),
        "worst_trade_pnl": round(min(pnls), 2),
        "average_r": round(mean(r_multiples), 4) if r_multiples else None,
        "exposure_time_pct": exposure_time_pct,
        "starting_balance": round(starting_balance, 2),
        "final_balance": round(balance, 2),
        "equity_curve": [round(e, 2) for e in equity_curve],
    }


def average_metrics(metric_dicts: list[dict]) -> dict:
    averaged = {}
    for field in AVERAGEABLE_FIELDS:
        values = [m[field] for m in metric_dicts if m.get(field) is not None]
        averaged[field] = round(sum(values) / len(values), 4) if values else None
    return averaged
