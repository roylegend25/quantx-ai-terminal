import asyncio
from datetime import datetime, timezone

import pytest

from app.data_sources import downloader
from app.data_sources.validator import validate_candle_sequence
from app.db.session import SessionLocal


def _ms(year, month, day=1):
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


def _kline(timestamp, price=100.0):
    return [timestamp, str(price), str(price + 2), str(price - 2), str(price + 1), "10", timestamp, "100", 2]


def test_monthly_job_persists_and_round_trips_canonical_interval():
    db = SessionLocal()
    try:
        job = downloader.create_job("candles", "BTCUSDT", "1M", db=db)
        assert job["timeframe"] == "1M"
        persisted = next(item for item in downloader.list_jobs(db=db) if item["job_id"] == job["job_id"])
        assert persisted["timeframe"] == "1M"
    finally:
        db.close()


def test_retried_monthly_job_preserves_canonical_interval(monkeypatch):
    seen = []

    async def fake_handler(job, db):
        seen.append(job["timeframe"])
        return {"rows_fetched": 0, "rows_stored": 0}

    monkeypatch.setitem(downloader._HANDLERS, "candles", fake_handler)
    job = downloader.create_job("candles", "BTCUSDT", "1M")
    asyncio.run(downloader.execute_job(job["job_id"]))
    asyncio.run(downloader.execute_job(job["job_id"]))
    assert seen == ["1M", "1M"]


def test_monthly_handler_sends_exact_binance_interval_and_stores_canonical(monkeypatch):
    received = {}

    async def fake_fetch(symbol, interval, **kwargs):
        received.update(symbol=symbol, interval=interval, kwargs=kwargs)
        return [_kline(_ms(2024, 1)), _kline(_ms(2024, 2)), _kline(_ms(2024, 3))]

    stored = {}
    monkeypatch.setattr(downloader.binance_futures, "fetch_klines", fake_fetch)
    monkeypatch.setattr(downloader, "store_candles", lambda db, symbol, timeframe, provider, rows, quality: stored.update(timeframe=timeframe) or len(rows))
    monkeypatch.setattr(downloader, "_save_quality_report", lambda *args, **kwargs: None)

    result = asyncio.run(downloader._handle_candles({
        "symbol": "BTCUSDT", "timeframe": "1M", "provider": "binance_futures",
        "requested_start": _ms(2024, 1), "requested_end": _ms(2024, 3), "limit": 10,
    }, object()))

    assert received["interval"] == "1M"
    assert received["interval"] not in {"1mo", "1m", "monthly", "30d"}
    assert stored["timeframe"] == "1M"
    assert result["rows_fetched"] == 3


def test_monthly_validation_uses_calendar_boundaries_and_accepts_partial_current_month():
    rows = [_kline(_ms(2023, 12)), _kline(_ms(2024, 1)), _kline(_ms(2024, 2)), _kline(_ms(2024, 3))]
    clean, report = validate_candle_sequence(downloader.normalize_klines(rows), "1M", now_ms=_ms(2024, 3, 15))
    assert [row["time"] for row in clean] == [_ms(2023, 12), _ms(2024, 1), _ms(2024, 2), _ms(2024, 3)]
    assert report["missing_candles"] == 0
    assert report["stale"] is False


def test_monthly_validation_counts_missing_calendar_month_without_interpolation():
    rows = downloader.normalize_klines([_kline(_ms(2024, 1)), _kline(_ms(2024, 3))])
    clean, report = validate_candle_sequence(rows, "1M", now_ms=_ms(2024, 3, 15))
    assert len(clean) == 2
    assert report["missing_candles"] == 1
    assert report["gaps"][0]["start"] == _ms(2024, 2)


@pytest.mark.parametrize("timeframe", ["1m", "5m", "15m", "1h", "4h", "1d", "1w"])
def test_existing_intervals_remain_canonical(timeframe):
    assert downloader.parse_timeframe(timeframe).value == timeframe
