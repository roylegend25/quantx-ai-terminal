"""Phase 20 data engine: validator, interpolator, storage round-trip and the
feature engine - all offline, no network."""

import numpy as np
import pytest

from app.data_sources.interpolator import fill_gaps
from app.data_sources.validator import validate_candles, find_gaps
from app.data_sources import downloader
from app.db.session import SessionLocal

TF_MS = 5 * 60 * 1000


def _candles(n=300, start=1_700_000_000_000, price=100.0, seed=7):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        price = max(1.0, price + rng.normal(0, 0.3))
        high = price + abs(rng.normal(0, 0.2))
        low = max(0.5, price - abs(rng.normal(0, 0.2)))
        rows.append(
            {
                "time": start + i * TF_MS,
                "open": price,
                "high": max(high, price),
                "low": min(low, price),
                "close": price,
                "volume": 100.0 + float(rng.uniform(0, 10)),
            }
        )
    return rows


def test_validator_clean_dataset_scores_high():
    rows = _candles()
    clean, report = validate_candles(rows, TF_MS, now_ms=rows[-1]["time"] + TF_MS)
    assert len(clean) == len(rows)
    assert report["quality_score"] > 90
    assert report["usable"] is True
    assert report["missing_candles"] == 0


def test_validator_counts_duplicates_broken_and_gaps():
    rows = _candles(100)
    rows.append(dict(rows[50]))  # duplicate
    rows[10] = {**rows[10], "high": rows[10]["low"] - 1}  # broken OHLC
    del rows[60:70]  # 10-bar gap

    clean, report = validate_candles(rows, TF_MS, now_ms=rows[-1]["time"] + TF_MS)
    assert report["duplicates"] == 1
    assert report["broken_ohlc"] == 1
    assert report["missing_candles"] >= 10  # the deleted range plus the broken bar's hole
    assert report["largest_gap_bars"] >= 10
    assert report["quality_score"] < 95


def test_validator_empty_dataset_unusable():
    clean, report = validate_candles([], TF_MS)
    assert clean == []
    assert report["quality_score"] == 0.0
    assert report["usable"] is False


def test_interpolator_fills_small_gaps_only():
    rows = _candles(50)
    small_gap_rows = rows[:10] + rows[12:30] + rows[40:]  # 2-bar gap and 10-bar gap

    filled, count, actions = fill_gaps(small_gap_rows, TF_MS, max_gap_bars=3)
    assert count == 2  # only the small gap
    interpolated = [r for r in filled if r.get("interpolated")]
    assert len(interpolated) == 2
    assert all(r["volume"] == 0.0 for r in interpolated)
    assert {a["action"] for a in actions} == {"interpolated", "rejected"}
    # rejected gap stays a gap
    assert find_gaps(filled, TF_MS)


def test_store_and_load_candles_round_trip():
    # distinct epoch base: other test modules also store BTCUSDT/5m and the
    # temp DB is shared across the whole session
    rows = _candles(120, start=1_650_000_000_000)
    db = SessionLocal()
    try:
        stored = downloader.store_candles(db, "BTCUSDT", "5m", "binance_futures", rows, quality=99.0)
        assert stored == 120
        # idempotent: re-storing inserts nothing new
        assert downloader.store_candles(db, "BTCUSDT", "5m", "binance_futures", rows, quality=99.0) == 0

        loaded = downloader.load_candles(
            "BTCUSDT", "5m", start_ms=rows[0]["time"], end_ms=rows[-1]["time"], db=db
        )
        assert len(loaded) == 120
        assert loaded[0]["time"] == rows[0]["time"]
        assert loaded[-1]["close"] == pytest.approx(rows[-1]["close"])

        coverage = downloader.candle_coverage(db=db)
        assert any(c["symbol"] == "BTCUSDT" and c["timeframe"] == "5m" and c["rows"] >= 120 for c in coverage)
    finally:
        db.close()


def test_feature_engine_generates_and_stores():
    from app.features import engine as feature_engine

    rows = _candles(400)
    db = SessionLocal()
    try:
        downloader.store_candles(db, "ETHUSDT", "1h", "binance_futures", rows, quality=98.0)
        summary = feature_engine.generate_and_store("ETHUSDT", "1h", db=db)
        assert summary["bars_used"] == 400
        assert summary["rows_stored"] > 0

        latest = summary["latest"]
        for key in ("rsi", "macd", "ema20", "sma200", "atr", "adx", "supertrend_direction",
                    "bb_width", "vwap", "trend_score", "momentum_score", "mean_reversion_score", "regime"):
            assert key in latest, f"missing feature {key}"
        assert latest["regime"] in ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY")
        assert latest["rsi"] is None or 0 <= latest["rsi"] <= 100

        snapshots = feature_engine.load_snapshots("ETHUSDT", "1h", db=db)
        assert snapshots
        assert snapshots[-1]["features"]["close"] == pytest.approx(rows[-1]["close"])
    finally:
        db.close()


def test_feature_engine_refuses_thin_data():
    from app.features import engine as feature_engine

    db = SessionLocal()
    try:
        downloader.store_candles(db, "XRPUSDT", "1d", "binance_futures", _candles(10), quality=99.0)
        with pytest.raises(feature_engine.InsufficientDataError):
            feature_engine.generate_and_store("XRPUSDT", "1d", db=db)
    finally:
        db.close()


def test_create_job_validates_inputs():
    with pytest.raises(ValueError):
        downloader.create_job(data_type="nonsense", symbol="BTCUSDT")
    with pytest.raises(ValueError):
        downloader.create_job(data_type="candles", symbol="DOGEUSDT", timeframe="5m")
    with pytest.raises(ValueError):
        downloader.create_job(data_type="candles", symbol="BTCUSDT", timeframe="7m")

    job = downloader.create_job(data_type="candles", symbol="btcusdt", timeframe="5m", limit=100)
    assert job["status"] == "queued"
    assert job["symbol"] == "BTCUSDT"
    assert downloader.list_jobs(limit=5)[0]["job_id"] == job["job_id"]
