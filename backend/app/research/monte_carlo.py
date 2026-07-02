"""Monte Carlo trade-sequence risk simulation.

Given a set of already-closed trades, repeatedly shuffles the order those
trades could have landed in (the trades themselves are fixed - only the
sequence varies) to see how much sequence-of-returns risk the strategy is
exposed to. This is a risk-analysis tool over historical/backtested trades;
it never generates new predictions or touches the live prediction path.
"""

import math
import random
import statistics

from app.research.metrics import MIN_CAGR_SPAN_YEARS, years_between

DEFAULT_SIMULATIONS = 1000
DEFAULT_RUIN_DRAWDOWN_PCT = 90.0


def _percentile(sorted_values: list[float], p: float) -> float | None:
    if not sorted_values:
        return None
    k = (len(sorted_values) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_values[int(k)]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def run(
    trades: list[dict],
    starting_balance: float = 10000.0,
    simulations: int = DEFAULT_SIMULATIONS,
    ruin_drawdown_pct: float = DEFAULT_RUIN_DRAWDOWN_PCT,
    seed: int | None = None,
) -> dict:
    if len(trades) < 5:
        raise ValueError(
            f"Need at least 5 closed trades to run a Monte Carlo simulation, got {len(trades)}."
        )

    pnls = [float(t.get("pnl") or 0.0) for t in trades]
    entry_times = [t["entry_time"] for t in trades if t.get("entry_time")]
    exit_times = [t["exit_time"] for t in trades if t.get("exit_time")]
    years = years_between(min(entry_times) if entry_times else None, max(exit_times) if exit_times else None)

    rng = random.Random(seed)

    drawdowns = []
    finals = []
    cagrs = []
    ruin_count = 0

    for _ in range(simulations):
        shuffled = pnls[:]
        rng.shuffle(shuffled)

        balance = starting_balance
        peak = balance
        max_dd = 0.0
        ruined = False

        for pnl in shuffled:
            balance += pnl
            peak = max(peak, balance)
            dd = (peak - balance) / peak * 100 if peak > 0 else 100.0
            max_dd = max(max_dd, dd)
            if dd >= ruin_drawdown_pct:
                ruined = True

        drawdowns.append(max_dd)
        finals.append(balance)
        if ruined or balance <= 0:
            ruin_count += 1
        if years >= MIN_CAGR_SPAN_YEARS and balance > 0:
            cagrs.append(((balance / starting_balance) ** (1 / years) - 1) * 100)

    finals.sort()

    return {
        "simulations": simulations,
        "trades_per_simulation": len(pnls),
        "starting_balance": round(starting_balance, 2),
        "worst_drawdown_pct": round(max(drawdowns), 2) if drawdowns else None,
        "best_drawdown_pct": round(min(drawdowns), 2) if drawdowns else None,
        "median_cagr_pct": round(statistics.median(cagrs), 2) if cagrs else None,
        "risk_of_ruin_pct": round(ruin_count / simulations * 100, 2),
        "ruin_drawdown_threshold_pct": ruin_drawdown_pct,
        "median_final_balance": round(statistics.median(finals), 2) if finals else None,
        "final_balance_confidence_interval_90pct": (
            [round(_percentile(finals, 0.05), 2), round(_percentile(finals, 0.95), 2)] if finals else None
        ),
    }
