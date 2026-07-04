from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.research import lab_engine, lab_strategies, optimizer
from app.research.execution_sim import CostConfig, RiskConfig, simulate_trades
from app.research.lab_repository import repository as lab_repository
from app.research.metrics import compute_metrics


def _synthetic_ohlc(n: int = 250, start_price: float = 100.0, trend: float = 0.05, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    price = start_price
    rows = []
    for i in range(n):
        price = max(1.0, price + trend + rng.normal(0, 0.3))
        high = price + abs(rng.normal(0, 0.2))
        low = max(0.5, price - abs(rng.normal(0, 0.2)))
        rows.append(
            {
                "time": start_ms + i * 5 * 60 * 1000,
                "open": price,
                "high": high,
                "low": low,
                "close": price,
                "volume": 100.0,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def synthetic_history(monkeypatch, tmp_path):
    df = _synthetic_ohlc()
    csv_path = tmp_path / "history.csv"
    df.to_csv(csv_path, index=False)
    monkeypatch.setattr(lab_engine, "history_path", lambda symbol, timeframe: csv_path)
    return df


def test_load_history_missing_file_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(lab_engine, "history_path", lambda symbol, timeframe: tmp_path / "missing.csv")
    with pytest.raises(FileNotFoundError):
        lab_engine.load_history("BTCUSDT", "5m")


def test_generate_signals_covers_every_non_ml_strategy(synthetic_history):
    df = lab_engine.load_history("BTCUSDT", "5m")
    for strategy in ["trend", "momentum", "mean_reversion", "breakout", "ensemble"]:
        signals = lab_strategies.generate_signals(strategy, df)
        assert len(signals) == len(df)
        assert all(direction in ("LONG", "SHORT", "NO_TRADE") for direction, _ in signals)


def test_champion_and_challenger_ml_raise_when_no_model_registered(synthetic_history):
    df = lab_engine.load_history("BTCUSDT", "5m")
    with pytest.raises(lab_strategies.ModelNotAvailableError):
        lab_strategies.generate_signals("champion_ml", df)
    with pytest.raises(lab_strategies.ModelNotAvailableError):
        lab_strategies.generate_signals("challenger_ml", df)


def test_unknown_strategy_rejected(synthetic_history):
    df = lab_engine.load_history("BTCUSDT", "5m")
    with pytest.raises(ValueError):
        lab_strategies.generate_signals("not_a_real_strategy", df)


def test_execution_sim_applies_commission_slippage_and_signal_flip_exit():
    df = pd.DataFrame(
        {
            "time": [0, 300000, 600000],
            "open": [100.0, 101.0, 99.0],
            "high": [100.5, 101.5, 99.5],
            "low": [99.5, 100.5, 98.5],
            "close": [100.0, 101.0, 99.0],
            "atr": [1.0, 1.0, 1.0],
        }
    )
    signals = [("LONG", 100.0), ("NO_TRADE", 0.0), ("SHORT", 100.0)]
    cost = CostConfig(commission_pct=0.1, slippage_bps=10, spread_bps=10, funding_rate_pct=0.0, latency_ms=0.0)
    risk = RiskConfig(atr_sl_mult=5.0, atr_tp_mult=5.0, entry_confidence_threshold=50.0, position_size_usd=1000.0)

    # a SHORT signal flips the open LONG - the close is booked as one trade,
    # and (matching app/backtest/engine.py's existing stop-and-reverse
    # convention) the opposite SHORT opens immediately in the same bar, which
    # then gets marked to market since the data ends right there
    trades = simulate_trades(df, signals, cost=cost, risk=risk)
    assert len(trades) == 2
    closed_long = trades[0]
    assert closed_long["side"] == "LONG"
    assert closed_long["exit_reason"] == "signal_flip"
    assert closed_long["commission"] > 0
    # entering ~100 and exiting ~99 after spread/slippage/commission must be a net loss
    assert closed_long["pnl"] < 0
    assert trades[1]["side"] == "SHORT"
    assert trades[1]["exit_reason"] == "end_of_data"


def test_execution_sim_stop_loss_wins_over_take_profit_on_the_same_bar():
    df = pd.DataFrame(
        {
            "time": [0, 300000],
            "open": [100.0, 100.0],
            "high": [100.5, 200.0],  # would also satisfy the take-profit...
            "low": [99.5, 50.0],  # ...but the stop-loss is checked first (conservative)
            "close": [100.0, 100.0],
            "atr": [1.0, 1.0],
        }
    )
    signals = [("LONG", 100.0), ("NO_TRADE", 0.0)]
    cost = CostConfig(commission_pct=0.0, slippage_bps=0.0, spread_bps=0.0, funding_rate_pct=0.0, latency_ms=0.0)
    risk = RiskConfig(atr_sl_mult=2.0, atr_tp_mult=2.0, entry_confidence_threshold=50.0, position_size_usd=1000.0)

    trades = simulate_trades(df, signals, cost=cost, risk=risk)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "stop_loss"
    assert trades[0]["r_multiple"] == pytest.approx(-1.0, abs=0.01)


def test_execution_sim_marks_open_position_to_market_at_end_of_data():
    df = pd.DataFrame(
        {
            "time": [0, 300000],
            "open": [100.0, 105.0],
            "high": [100.5, 105.5],
            "low": [99.5, 104.5],
            "close": [100.0, 105.0],
            "atr": [1.0, 1.0],
        }
    )
    signals = [("LONG", 100.0), ("LONG", 100.0)]
    risk = RiskConfig(atr_sl_mult=50.0, atr_tp_mult=50.0, entry_confidence_threshold=50.0, position_size_usd=1000.0)

    trades = simulate_trades(df, signals, cost=CostConfig(commission_pct=0.0, slippage_bps=0.0, spread_bps=0.0, funding_rate_pct=0.0), risk=risk)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "end_of_data"


def test_metrics_report_best_worst_trade_and_average_r():
    trades = [
        {"pnl": 100.0, "r_multiple": 2.0},
        {"pnl": -50.0, "r_multiple": -1.0},
    ]
    metrics = compute_metrics(trades)
    assert metrics["best_trade_pnl"] == 100.0
    assert metrics["worst_trade_pnl"] == -50.0
    assert metrics["average_r"] == 0.5


def test_run_backtest_persists_and_lists_without_trade_detail(synthetic_history):
    result = lab_engine.run_backtest(strategy="trend", symbol="BTCUSDT", timeframe="5m")
    assert result["bars"] == len(synthetic_history)
    assert "equity_curve" in result and "drawdown_curve" in result
    assert len(result["drawdown_curve"]) == len(result["equity_curve"])

    saved = lab_repository.save(
        run=result,
        parameters={"atr_sl_mult": 1.5, "atr_tp_mult": 3.0, "entry_confidence_threshold": 50.0, "position_size_usd": 1000.0},
        fees={
            "commission_pct": 0.04,
            "slippage_bps": 2.0,
            "spread_bps": 2.0,
            "funding_rate_pct": 0.01,
            "latency_ms": 250.0,
            "partial_fill_ratio": 1.0,
        },
    )
    assert saved["experiment_id"]

    fetched = lab_repository.get(saved["experiment_id"])
    assert fetched is not None
    assert fetched["strategy"] == "trend"
    assert "trades" in fetched["results"]

    summary = lab_repository.list_all(strategy="trend")
    assert any(e["experiment_id"] == saved["experiment_id"] for e in summary)
    assert "trades" not in next(e for e in summary if e["experiment_id"] == saved["experiment_id"])["results"]


def test_optimizer_grid_search_ranks_by_score(synthetic_history):
    result = optimizer.optimize(
        strategy="trend",
        symbol="BTCUSDT",
        timeframe="5m",
        sl_mults=[1.0, 2.0],
        tp_mults=[2.0],
        entry_thresholds=[50.0],
    )
    assert result["combinations_tested"] == 2
    assert result["best"] is not None
    scores = [r["score"] for r in result["results"]]
    assert scores == sorted(scores, reverse=True)


def test_walkforward_generalizes_across_strategies(synthetic_history):
    result = lab_engine.run_walkforward(
        strategy="momentum",
        symbol="BTCUSDT",
        timeframe="5m",
        train_bars=50,
        validate_bars=50,
    )
    assert result["windows_run"] > 0
    assert result["strategy"] == "momentum"
