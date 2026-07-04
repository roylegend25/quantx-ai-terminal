"""Parameter optimization for the Research Lab (Phase 16) - a grid search
over execution-level risk parameters (ATR stop-loss/take-profit multiples
and the entry confidence threshold) that apply uniformly to every strategy
app/research/lab_strategies.py supports, without reimplementing any
strategy's internal decision thresholds. Signals are generated once per
strategy/symbol/timeframe and replayed under every risk combination, so this
only re-runs the (cheap) execution simulator per grid point, not strategy
inference. Ranks candidates by Sharpe ratio (falling back to total return
when Sharpe can't be computed) and returns the full grid, not just the
winner, so a caller can inspect trade-offs.
"""

from itertools import product

from app.research import execution_sim, lab_engine, lab_strategies
from app.research.metrics import compute_metrics

DEFAULT_SL_MULTS = [1.0, 1.5, 2.0, 2.5]
DEFAULT_TP_MULTS = [2.0, 3.0, 4.0]
DEFAULT_ENTRY_THRESHOLDS = [40.0, 50.0, 60.0]


def _score(metrics: dict) -> float:
    if metrics.get("sharpe_ratio") is not None:
        return metrics["sharpe_ratio"]
    return (metrics.get("total_return_pct") or 0.0) / 100


def optimize(
    strategy: str,
    symbol: str,
    timeframe: str,
    start_date: str | None = None,
    end_date: str | None = None,
    cost: execution_sim.CostConfig | None = None,
    starting_balance: float = 10000.0,
    position_size_usd: float = 1000.0,
    sl_mults: list[float] | None = None,
    tp_mults: list[float] | None = None,
    entry_thresholds: list[float] | None = None,
    db=None,
) -> dict:
    cost = cost or execution_sim.CostConfig()
    sl_mults = sl_mults or DEFAULT_SL_MULTS
    tp_mults = tp_mults or DEFAULT_TP_MULTS
    entry_thresholds = entry_thresholds or DEFAULT_ENTRY_THRESHOLDS

    df = lab_engine.load_history(symbol, timeframe, start_date, end_date)
    signals = lab_strategies.generate_signals(strategy, df, db=db)

    results = []
    for sl_mult, tp_mult, threshold in product(sl_mults, tp_mults, entry_thresholds):
        risk = execution_sim.RiskConfig(
            atr_sl_mult=sl_mult,
            atr_tp_mult=tp_mult,
            entry_confidence_threshold=threshold,
            position_size_usd=position_size_usd,
        )
        trades = execution_sim.simulate_trades(df, signals, cost=cost, risk=risk)
        metrics = compute_metrics(trades, starting_balance=starting_balance)
        results.append(
            {
                "atr_sl_mult": sl_mult,
                "atr_tp_mult": tp_mult,
                "entry_confidence_threshold": threshold,
                "score": round(_score(metrics), 4),
                "metrics": metrics,
            }
        )

    results.sort(key=lambda r: r["score"], reverse=True)
    return {
        "strategy": strategy,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "combinations_tested": len(results),
        "best": results[0] if results else None,
        "results": results,
    }
