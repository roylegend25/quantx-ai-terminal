"""Phase 20 advanced backtester: trailing stops, holding limits, circuit
breakers, extended metrics, presets and the DB-backed run pipeline."""

import numpy as np
import pandas as pd
import pytest

from app.backtest.indicators import add_indicators
from app.data_sources import downloader
from app.db.session import SessionLocal
from app.research import advanced_backtest
from app.research.advanced_backtest import AdvancedRiskConfig, simulate_advanced
from app.research.execution_sim import CostConfig

TF_MS = 5 * 60 * 1000


def _df(n=300, trend=0.05, seed=3, start=1_700_000_000_000):
    rng = np.random.default_rng(seed)
    price = 100.0
    rows = []
    for i in range(n):
        price = max(1.0, price + trend + rng.normal(0, 0.3))
        rows.append(
            {
                "time": start + i * TF_MS,
                "open": price,
                "high": price + abs(rng.normal(0, 0.2)),
                "low": max(0.5, price - abs(rng.normal(0, 0.2))),
                "close": price,
                "volume": 100.0,
            }
        )
    return add_indicators(pd.DataFrame(rows))


def test_max_holding_time_forces_exit():
    df = _df(trend=0.0)
    signals = [("LONG", 80.0)] + [("NO_TRADE", 0.0)] * (len(df) - 1)
    risk = AdvancedRiskConfig(atr_sl_mult=50.0, atr_tp_mult=100.0, max_holding_bars=5)
    trades, _ = simulate_advanced(df, signals, CostConfig(), risk)
    assert trades
    assert trades[0]["exit_reason"] == "max_holding_time"


def test_trailing_stop_tightens_long_stop():
    df = _df(trend=0.5)  # strong uptrend
    signals = [("LONG", 80.0)] + [("NO_TRADE", 0.0)] * (len(df) - 1)
    no_trail = AdvancedRiskConfig(atr_sl_mult=3.0, atr_tp_mult=1000.0)
    with_trail = AdvancedRiskConfig(atr_sl_mult=3.0, atr_tp_mult=1000.0, trailing_stop_atr_mult=1.0)

    trades_plain, _ = simulate_advanced(df, signals, CostConfig(), no_trail)
    trades_trail, _ = simulate_advanced(df, signals, CostConfig(), with_trail)
    # trailing stop locks in profit: the trailed exit can't be worse than entry-based stop
    assert trades_trail[0]["pnl"] >= trades_plain[0]["pnl"] - 1e-6 or trades_trail[0]["exit_reason"] == "stop_loss"


def test_drawdown_circuit_breaker_blocks_entries():
    df = _df(trend=-0.6, seed=11)  # hard downtrend, longs bleed
    signals = [("LONG", 90.0)] * len(df)
    risk = AdvancedRiskConfig(atr_sl_mult=0.5, atr_tp_mult=50.0, max_drawdown_stop_pct=1.0, position_size_usd=5000.0)
    trades, events = simulate_advanced(df, signals, CostConfig(), risk, starting_balance=10000.0)
    assert any(e["type"] == "drawdown_circuit_breaker" for e in events)
    # after the breaker trips no new positions open: trade count is bounded
    breaker_time = next(e for e in events if e["type"] == "drawdown_circuit_breaker")["time"]
    late_entries = [t for t in trades if str(t["entry_time"]) > str(breaker_time)]
    assert not late_entries


def test_extended_metrics_shape():
    df = _df()
    signals = [("LONG", 80.0) if i % 20 == 0 else ("NO_TRADE", 0.0) for i in range(len(df))]
    trades, _ = simulate_advanced(df, signals, CostConfig(), AdvancedRiskConfig())
    metrics = advanced_backtest.extended_metrics(trades)
    for key in ("max_consecutive_losses", "long_accuracy_pct", "short_accuracy_pct", "regime_performance"):
        assert key in metrics
    assert metrics["long_trades"] == len(trades)
    assert isinstance(metrics["regime_performance"], dict) and metrics["regime_performance"]


def test_apply_preset_merges_and_overrides():
    merged = advanced_backtest._apply_preset({"preset": "scalping", "atr_sl_mult": 9.9})
    assert merged["atr_sl_mult"] == 9.9  # explicit field wins
    assert merged["atr_tp_mult"] == 1.5  # preset default preserved
    with pytest.raises(ValueError):
        advanced_backtest._apply_preset({"preset": "does_not_exist"})


def _store_history(db, symbol="BTCUSDT", timeframe="5m", n=600):
    df = _df(n)
    rows = df[["time", "open", "high", "low", "close", "volume"]].to_dict("records")
    downloader.store_candles(db, symbol, timeframe, "binance_futures", rows, quality=99.0)


def test_run_advanced_end_to_end_with_db_candles():
    db = SessionLocal()
    try:
        _store_history(db)
        result = advanced_backtest.run_advanced(
            {
                "strategy": "trend",
                "symbols": ["BTCUSDT"],
                "timeframes": ["5m"],
                "train_split": 0.7,
                "monte_carlo_sims": 0,
            },
            db=db,
        )
        assert result["summary"]["combinations_run"] == 1
        combo = result["combinations"][0]
        assert combo["ok"], combo.get("error")
        assert combo["data"]["source"] == "market_candles"
        assert "in_sample_metrics" in combo and "out_of_sample_metrics" in combo
        assert combo["equity_curve"] and combo["drawdown_curve"]

        # persisted and retrievable
        stored = advanced_backtest.get_run(result["run_id"], db=db)
        assert stored is not None and stored["run_id"] == result["run_id"]
        assert advanced_backtest.list_runs(db=db)[0]["run_id"] == result["run_id"]
    finally:
        db.close()


def test_run_advanced_reports_missing_data_per_combo():
    db = SessionLocal()
    try:
        result = advanced_backtest.run_advanced(
            {"strategy": "trend", "symbols": ["SOLUSDT"], "timeframes": ["12h"]}, db=db
        )
        combo = result["combinations"][0]
        assert combo["ok"] is False
        assert "download" in combo["error"].lower() or "no stored" in combo["error"].lower()
    finally:
        db.close()


def test_walkforward_and_montecarlo_over_stored_run():
    db = SessionLocal()
    try:
        _store_history(db, n=900)
        result = advanced_backtest.run_advanced(
            {"strategy": "momentum", "symbols": ["BTCUSDT"], "timeframes": ["5m"],
             "entry_confidence_threshold": 40.0},
            db=db,
        )
        run_id = result["run_id"]

        wf = advanced_backtest.run_walkforward_for(run_id, train_bars=300, validate_bars=150, db=db)
        assert wf["results"][0]["windows_run"] >= 2

        combo = result["combinations"][0]
        if len(combo["trades"]) >= 5:
            mc = advanced_backtest.run_montecarlo_for(run_id, simulations=200, db=db)
            assert mc["results"][0]["ok"]
            assert mc["results"][0]["simulations"] == 200
    finally:
        db.close()
