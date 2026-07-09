"""Phase 20 hardening: the data-quality safety gate on the live prediction/
trading path, the optimizer API wiring, and optional-provider degradation."""

import time

import numpy as np
import pandas as pd

from app.api.prediction import _data_quality_block, make_prediction
from app.backtest.indicators import add_indicators
from app.data_sources import cryptoquant, glassnode, hyblock
from app.db.models import DataQualityReport
from app.db.session import SessionLocal
from app.research import optimizer
from app.trading import risk_manager

TF_MS = 5 * 60 * 1000

FEATURES = {
    "price": 100.0,
    "ema20": 95.0,
    "ema50": 90.0,
    "ema200": 85.0,
    "rsi": 65.0,
    "macd_hist": 1.0,
    "atr": 2.0,
    "bb_width": 0.03,
    "realized_volatility": 0.01,
    "trend_score": 3,
    "regime": "bullish_trend",
}

LIVE_OK = {"source": "binance_live", "provider_ok": True, "provider_error": None}
CACHED = {"source": "cached_db", "provider_ok": False, "provider_error": "ConnectError('boom')"}


def _candles(n=10, end_ms=None, tf_ms=TF_MS):
    end_ms = end_ms or int(time.time() * 1000)
    return [
        {"time": end_ms - (n - 1 - i) * tf_ms, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 50.0}
        for i in range(n)
    ]


# ---------------------------------------------------------------- make_prediction gate

def test_unreliable_data_forces_no_trade_and_blocks_risk():
    dq = {"reliable": False, "reason": "Cached BTCUSDT 5m data quality 12.0 is below the usable threshold 60.0 - trading blocked"}
    result = make_prediction(FEATURES, data_quality=dq)
    assert result["direction"] == "NO_TRADE"
    assert result["confidence"] == 0.0
    assert result["risk"]["allowed"] is False
    assert result["risk"]["reason"] == dq["reason"]
    assert result["data_quality"] == dq


def test_reliable_data_does_not_change_prediction():
    baseline = make_prediction(FEATURES)
    gated = make_prediction(FEATURES, data_quality={"reliable": True, "reason": None})
    assert gated["direction"] == baseline["direction"]
    assert gated["confidence"] == baseline["confidence"]


# ---------------------------------------------------------------- risk_manager gate

def _portfolio():
    return {"balance": 10000.0, "daily_pnl": 0.0}


def test_evaluate_risk_vetoes_on_unreliable_data():
    decision = risk_manager.evaluate_risk(
        confidence=99.0,
        direction="LONG",
        open_positions=0,
        portfolio=_portfolio(),
        data_reliable=False,
        data_reason="Provider unavailable and cached 5m data is 240m old (limit 15m) - NO_TRADE",
    )
    assert decision["allowed"] is False
    assert "240m old" in decision["reason"]


def test_evaluate_risk_fails_open_when_data_verdict_unknown():
    decision = risk_manager.evaluate_risk(
        confidence=99.0,
        direction="LONG",
        open_positions=0,
        portfolio=_portfolio(),
        data_reliable=None,
    )
    # None = no verdict available; the data gate must not be the blocker
    assert "data" not in decision["reason"].lower() or decision["allowed"]


# ---------------------------------------------------------------- _data_quality_block

def test_live_source_is_reliable():
    block = _data_quality_block("BTCUSDT", "5m", LIVE_OK, _candles())
    assert block["reliable"] is True
    assert block["source"] == "binance_live"


def test_no_candles_at_all_is_unreliable():
    block = _data_quality_block("BTCUSDT", "5m", CACHED, [])
    assert block["reliable"] is False
    assert "no cached candles" in block["reason"].lower()


def test_fresh_cache_is_reliable_but_stale_cache_is_not():
    fresh = _data_quality_block("BTCUSDT", "5m", CACHED, _candles())
    assert fresh["reliable"] is True

    stale_end = int(time.time() * 1000) - 4 * TF_MS  # older than the 3-interval limit
    stale = _data_quality_block("BTCUSDT", "5m", CACHED, _candles(end_ms=stale_end))
    assert stale["reliable"] is False
    assert "NO_TRADE" in stale["reason"]


def test_fresh_cache_with_bad_quality_report_is_unreliable():
    db = SessionLocal()
    try:
        report = DataQualityReport(
            symbol="BTCUSDT", timeframe="5m", quality_score=10.0, timestamp=int(time.time() * 1000)
        )
        db.add(report)
        db.commit()

        block = _data_quality_block("BTCUSDT", "5m", CACHED, _candles())
        assert block["reliable"] is False
        assert "below the usable threshold" in block["reason"]
        assert block["quality_score"] == 10.0
    finally:
        db.query(DataQualityReport).filter(DataQualityReport.symbol == "BTCUSDT").delete()
        db.commit()
        db.close()


# ---------------------------------------------------------------- optimizer wiring

def _history_df(n=400, seed=5):
    rng = np.random.default_rng(seed)
    price = 100.0
    rows = []
    for i in range(n):
        price = max(1.0, price + 0.05 + rng.normal(0, 0.3))
        rows.append(
            {
                "time": 1_700_000_000_000 + i * TF_MS,
                "open": price,
                "high": price + abs(rng.normal(0, 0.2)),
                "low": max(0.5, price - abs(rng.normal(0, 0.2))),
                "close": price,
                "volume": 100.0,
            }
        )
    return add_indicators(pd.DataFrame(rows))


def test_optimizer_accepts_preloaded_df_and_ranks_grid():
    result = optimizer.optimize(
        strategy="trend",
        symbol="BTCUSDT",
        timeframe="5m",
        sl_mults=[1.0, 2.0],
        tp_mults=[2.0],
        entry_thresholds=[40.0],
        df=_history_df(),
    )
    assert result["combinations_tested"] == 2
    assert result["best"] is not None
    scores = [r["score"] for r in result["results"]]
    assert scores == sorted(scores, reverse=True)


def test_optimizer_rejects_oversized_grid():
    import pytest

    with pytest.raises(ValueError):
        optimizer.optimize(
            strategy="trend",
            symbol="BTCUSDT",
            timeframe="5m",
            sl_mults=[float(i) for i in range(10)],
            tp_mults=[float(i) for i in range(10)],
            entry_thresholds=[float(i) for i in range(3)],
            df=_history_df(n=120),
        )


# ---------------------------------------------------------------- optional providers

def test_optional_providers_degrade_without_keys(monkeypatch):
    monkeypatch.delenv("HYBLOCK_API_KEY", raising=False)
    monkeypatch.delenv("CRYPTOQUANT_API_KEY", raising=False)
    monkeypatch.delenv("GLASSNODE_API_KEY", raising=False)

    for mod, key in ((hyblock, "HYBLOCK_API_KEY"), (cryptoquant, "CRYPTOQUANT_API_KEY"), (glassnode, "GLASSNODE_API_KEY")):
        status = mod.status()
        assert status["available"] is False
        assert key in status["reason"]

    monkeypatch.setenv("GLASSNODE_API_KEY", "test-key")
    assert glassnode.status()["available"] is True
