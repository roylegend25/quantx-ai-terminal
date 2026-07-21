import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException

from app.api import data as data_api
from app.api import prediction as prediction_api
from app.data_sources import downloader
from app.data_sources.validator import validate_candle_sequence
from app.db.models import MarketCandle
from app.db.session import SessionLocal
from app.timeframes.canonical import cache_key, timeframe_capabilities


def _ms(year, month, day=1):
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


def _rows(*timestamps):
    return [{"time": timestamp, "open": 100.0, "high": 102.0, "low": 99.0,
             "close": 101.0, "volume": 10.0} for timestamp in timestamps]


def test_public_data_api_returns_last_twelve_months_and_week_alias():
    db = SessionLocal()
    try:
        db.query(MarketCandle).filter_by(symbol="BTCUSDT", timeframe="1M").delete()
        timestamps = [_ms(year, month) for year in (2023, 2024) for month in range(1, 13)]
        downloader.store_candles(db, "BTCUSDT", "1M", "binance_futures", _rows(*timestamps), 100.0)
        response = asyncio.run(data_api.candles(symbol="BTCUSDT", timeframe="1M", limit=12,
                                                include_interpolated=True, db=db))
        assert response["timeframe"] == "1M" and response["timeframe_kind"] == "calendar"
        assert response["fixed_duration_ms"] is None
        assert response["count"] == 12
        assert [row["time"] for row in response["candles"]] == timestamps[-12:]
        assert response["validation"]["valid"] is True

        weekly = asyncio.run(data_api.candles(symbol="BTCUSDT", timeframe="1W", limit=10,
                                              include_interpolated=True, db=db))
        assert weekly["timeframe"] == "1w"
    finally:
        db.close()


def test_public_data_api_minute_unchanged_and_invalid_rejected():
    db = SessionLocal()
    try:
        response = asyncio.run(data_api.candles(symbol="BTCUSDT", timeframe="1m", limit=1,
                                                include_interpolated=True, db=db))
        assert response["timeframe"] == "1m" and response["fixed_duration_ms"] == 60_000
        with pytest.raises(HTTPException) as exc:
            asyncio.run(data_api.candles(symbol="BTCUSDT", timeframe="30d", limit=1,
                                         include_interpolated=True, db=db))
        assert exc.value.status_code == 422
        assert cache_key("1m") != cache_key("1M")
    finally:
        db.close()


@pytest.mark.parametrize("timestamps", [
    (_ms(2023, 1), _ms(2023, 2)), (_ms(2023, 2), _ms(2023, 3)),
    (_ms(2024, 2), _ms(2024, 3)), (_ms(2024, 4), _ms(2024, 5)),
    (_ms(2023, 12), _ms(2024, 1)),
])
def test_calendar_month_transitions_are_valid(timestamps):
    _, report = validate_candle_sequence(_rows(*timestamps), "1M", now_ms=timestamps[-1])
    assert report["valid"] is True and report["gap_count"] == 0


def test_monthly_validation_reports_gap_duplicate_order_and_bad_boundaries():
    _, gap = validate_candle_sequence(_rows(_ms(2024, 1), _ms(2024, 3)), "1M", now_ms=_ms(2024, 3))
    assert gap["validation_error"] == "MONTHLY_CANDLE_GAP"
    assert gap["gaps"][0]["expected_next"] == _ms(2024, 2)
    _, duplicate = validate_candle_sequence(_rows(_ms(2024, 1), _ms(2024, 1)), "1M")
    assert duplicate["validation_error"] == "MONTHLY_CANDLE_DUPLICATE"
    _, ordered = validate_candle_sequence(_rows(_ms(2024, 2), _ms(2024, 1)), "1M")
    assert ordered["validation_error"] == "MONTHLY_CANDLE_ORDER_INVALID"
    _, fixed_thirty = validate_candle_sequence(_rows(_ms(2024, 1), _ms(2024, 1) + 30 * 86_400_000), "1M")
    assert fixed_thirty["validation_error"] == "MONTHLY_CANDLE_BOUNDARY_INVALID"


def test_current_partial_month_is_valid():
    now = _ms(2024, 3, 15)
    _, report = validate_candle_sequence(_rows(_ms(2024, 2), _ms(2024, 3)), "1M", now_ms=now)
    assert report["valid"] is True and report["open_current_candle"] is True and report["stale"] is False


class _Response:
    def __init__(self, payload): self._payload = payload
    def raise_for_status(self): return None
    def json(self): return self._payload


class _Client:
    payload = []
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def get(self, *args, **kwargs): return _Response(self.payload)


@pytest.mark.parametrize("timestamps", [
    (_ms(2023, 2), _ms(2023, 3)), (_ms(2024, 2), _ms(2024, 3)), (_ms(2024, 1), _ms(2024, 2)),
])
def test_live_monthly_ingestion_persists_variable_months(monkeypatch, timestamps):
    _Client.payload = [[row["time"], "100", "102", "99", "101", "10"] for row in _rows(*timestamps)]
    monkeypatch.setattr(prediction_api.httpx, "AsyncClient", lambda **kwargs: _Client())
    db = SessionLocal(); db.query(MarketCandle).filter_by(symbol="ETHUSDT", timeframe="1M").delete(); db.commit(); db.close()
    candles, provenance = asyncio.run(prediction_api._fetch_candles_with_fallback("ETHUSDT", "1M", 20))
    assert provenance["source"] == "binance_live" and len(candles) == 2
    db = SessionLocal()
    try:
        stored = db.query(MarketCandle).filter_by(symbol="ETHUSDT", timeframe="1M").all()
        assert {row.timestamp for row in stored} == set(timestamps)
    finally:
        db.close()


def test_downloader_and_prediction_share_canonical_validator():
    assert downloader.validate_candle_sequence is prediction_api.validate_candle_sequence
    monthly = timeframe_capabilities("1M")
    assert monthly.prediction_supported and monthly.data_api_supported and not monthly.execution_supported
    assert monthly.fixed_duration_ms is None


def test_provider_failure_reads_canonical_monthly_cache(monkeypatch):
    class FailingClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("offline", request=httpx.Request("GET", "https://fake.invalid"))

    monkeypatch.setattr(prediction_api.httpx, "AsyncClient", lambda **kwargs: FailingClient())
    db = SessionLocal()
    try:
        db.query(MarketCandle).filter_by(symbol="SOLUSDT", timeframe="1M").delete()
        downloader.store_candles(db, "SOLUSDT", "1M", "binance_futures",
                                 _rows(_ms(2024, 1), _ms(2024, 2)), 100.0)
    finally:
        db.close()
    candles, provenance = asyncio.run(prediction_api._fetch_candles_with_fallback("SOLUSDT", "1M", 12))
    assert provenance["source"] == "cached_db"
    assert [row["time"] for row in candles] == [_ms(2024, 1), _ms(2024, 2)]
