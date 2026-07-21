import asyncio

import pytest
from fastapi import HTTPException

from app.api import data as data_api
from app.db.session import SessionLocal


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("timeframe", ["1mo", "1MONTH", "15M", "", "1w ", "1WEEK"])
def test_invalid_feature_timeframe_returns_structured_422_before_data_access(
    monkeypatch, refresh, timeframe,
):
    calls = {"generate": 0, "load": 0}

    def forbidden_generate(*args, **kwargs):
        calls["generate"] += 1
        raise AssertionError("feature generation must not run for invalid input")

    def forbidden_load(*args, **kwargs):
        calls["load"] += 1
        raise AssertionError("snapshot storage must not run for invalid input")

    monkeypatch.setattr(data_api.feature_engine, "generate_and_store", forbidden_generate)
    monkeypatch.setattr(data_api.feature_engine, "load_snapshots", forbidden_load)
    db = SessionLocal()
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(data_api.features(symbol="BTCUSDT", timeframe=timeframe,
                                          limit=10, refresh=refresh, db=db))
    finally:
        db.close()
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "UNSUPPORTED_TIMEFRAME"
    assert error.value.detail["provided"] == timeframe
    assert "1m" in error.value.detail["supported"] and "1M" in error.value.detail["supported"]
    assert calls == {"generate": 0, "load": 0}


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("provided,canonical", [("1m", "1m"), ("1M", "1M"), ("1W", "1w")])
def test_valid_feature_timeframe_uses_one_canonical_storage_value(
    monkeypatch, refresh, provided, canonical,
):
    calls = []

    def generate(symbol, timeframe, db):
        calls.append(("generate", timeframe))
        return {"generated": True}

    def load(symbol, timeframe, limit, db):
        calls.append(("load", timeframe))
        return []

    monkeypatch.setattr(data_api.feature_engine, "generate_and_store", generate)
    monkeypatch.setattr(data_api.feature_engine, "load_snapshots", load)
    db = SessionLocal()
    try:
        response = asyncio.run(data_api.features(symbol="BTCUSDT", timeframe=provided,
                                                 limit=10, refresh=refresh, db=db))
    finally:
        db.close()
    assert response["timeframe"] == canonical
    assert calls == ([("generate", canonical), ("load", canonical)] if refresh
                     else [("load", canonical)])


def test_minute_and_monthly_feature_storage_keys_do_not_collide(monkeypatch):
    loaded = []
    monkeypatch.setattr(data_api.feature_engine, "load_snapshots",
                        lambda symbol, timeframe, limit, db: loaded.append(timeframe) or [])
    db = SessionLocal()
    try:
        asyncio.run(data_api.features(timeframe="1m", refresh=False, db=db))
        asyncio.run(data_api.features(timeframe="1M", refresh=False, db=db))
    finally:
        db.close()
    assert loaded == ["1m", "1M"] and loaded[0] != loaded[1]


def test_unexpected_snapshot_failure_after_valid_parsing_is_not_relabelled(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(data_api.feature_engine, "load_snapshots", fail)
    db = SessionLocal()
    try:
        with pytest.raises(RuntimeError, match="storage unavailable"):
            asyncio.run(data_api.features(timeframe="1m", refresh=False, db=db))
    finally:
        db.close()
