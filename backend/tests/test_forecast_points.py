"""Server-side forecast/band point generation (app/quant/forecast.py) and
its exposure through GET /api/prediction/{symbol} for BTCUSDT and ETHUSDT
across every timeframe the chart offers."""

import math

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import prediction as prediction_module
from app.api.prediction import router as prediction_router
from app.quant.forecast import HORIZON_BARS, build_forecast, horizon_bars

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}

BASE = dict(
    last_candle_time=1_700_000_000_000,
    price=100.0,
    confidence=60.0,
    target=104.0,
    stop=98.0,
    atr=1.5,
    realized_volatility=0.02,
    candle_count=220,
)


def _assert_series_valid(points, last_time, interval_ms):
    assert len(points) >= 3
    reference = last_time // 1000
    interval_seconds = interval_ms // 1000
    for index, p in enumerate(points):
        assert p["time"] == reference + index * interval_seconds
        assert isinstance(p["price"], float)
        assert math.isfinite(p["price"]) and p["price"] > 0


@pytest.mark.parametrize("interval", ["1m", "3m", "30m", "1h", "4h", "1d"])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_directional_forecast_produces_full_horizon(interval, direction):
    target = 104.0 if direction == "LONG" else 96.0
    stop = 98.0 if direction == "LONG" else 102.0
    fc = build_forecast(
        interval=interval, interval_ms=INTERVAL_MS[interval],
        direction=direction, **{**BASE, "target": target, "stop": stop},
    )

    bars = HORIZON_BARS[interval]
    assert fc["bars"] == bars
    assert fc["horizon_ms"] == bars * INTERVAL_MS[interval]
    assert fc["band_basis"] == "atr"
    assert fc["available"] is True
    assert fc["trade_actionable"] is True

    for key in ("forecast_points", "upper_band_points", "lower_band_points"):
        assert len(fc[key]) == bars + 1
        _assert_series_valid(fc[key], BASE["last_candle_time"], INTERVAL_MS[interval])

    # forecast converges on the target; bands bracket the forecast at
    # every step regardless of direction
    assert fc["forecast_points"][-1]["price"] == pytest.approx(target, rel=1e-6)
    for f, u, l in zip(fc["forecast_points"], fc["upper_band_points"], fc["lower_band_points"]):
        assert u["price"] >= f["price"] >= l["price"]


def test_no_trade_can_produce_non_actionable_informational_points():
    fc = build_forecast(interval="1h", interval_ms=INTERVAL_MS["1h"], direction="NO_TRADE",
                        informational_direction="BEARISH", informational_strength=.4, **BASE)
    assert fc["available"] is True
    assert fc["trade_actionable"] is False
    assert fc["forecast_type"] == "informational"
    assert fc["target_price"] is None and fc["invalidation_price"] is None
    _assert_series_valid(fc["median_path"], BASE["last_candle_time"], INTERVAL_MS["1h"])

def test_no_trade_without_source_composition_is_unavailable():
    fc = build_forecast(interval="1h", interval_ms=INTERVAL_MS["1h"], direction="NO_TRADE", **BASE)
    assert fc["available"] is False and fc["median_path"] == [] and fc["reason"]


def test_band_basis_fallback_chain():
    no_atr = build_forecast(
        interval="1h", interval_ms=INTERVAL_MS["1h"], direction="LONG",
        **{**BASE, "atr": None},
    )
    assert no_atr["band_basis"] == "realized_volatility"

    no_vol = build_forecast(
        interval="1h", interval_ms=INTERVAL_MS["1h"], direction="LONG",
        **{**BASE, "atr": None, "realized_volatility": None},
    )
    assert no_vol["band_basis"] == "fallback_pct"
    assert len(no_vol["upper_band_points"]) == HORIZON_BARS["1h"] + 1

    nan_atr = build_forecast(
        interval="1h", interval_ms=INTERVAL_MS["1h"], direction="LONG",
        **{**BASE, "atr": float("nan"), "realized_volatility": None},
    )
    assert nan_atr["band_basis"] == "fallback_pct"
    for p in nan_atr["forecast_points"]:
        assert math.isfinite(p["price"])


def test_higher_confidence_tightens_the_cone():
    low = build_forecast(interval="1h", interval_ms=INTERVAL_MS["1h"], direction="LONG", **{**BASE, "confidence": 10.0})
    high = build_forecast(interval="1h", interval_ms=INTERVAL_MS["1h"], direction="LONG", **{**BASE, "confidence": 90.0})
    low_width = low["upper_band_points"][-1]["price"] - low["lower_band_points"][-1]["price"]
    high_width = high["upper_band_points"][-1]["price"] - high["lower_band_points"][-1]["price"]
    assert high_width < low_width


def test_invalid_price_is_refused_not_fabricated():
    fc = build_forecast(
        interval="1h", interval_ms=INTERVAL_MS["1h"], direction="LONG",
        **{**BASE, "price": float("nan")},
    )
    assert fc["forecast_points"] == []
    assert "reference" in fc["reason"]


def test_every_supported_interval_has_a_horizon():
    for interval in ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"):
        assert horizon_bars(interval) > 0


# ---------------------------------------------------------- API route level

def _make_client():
    app = FastAPI()
    app.include_router(prediction_router)
    return TestClient(app)


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
@pytest.mark.parametrize("timeframe", ["1m", "3m", "30m", "1h", "4h", "1d"])
def test_prediction_route_exposes_consistent_forecast_fields(monkeypatch, symbol, timeframe):
    from tests.test_prediction_multi_symbol import (
        _FakeAsyncClient,
        _fake_evaluate_all_timeframes,
        _fake_get_context,
    )

    monkeypatch.setattr(prediction_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(prediction_module.market_intelligence, "get_context", _fake_get_context)
    monkeypatch.setattr(prediction_module, "evaluate_all_timeframes", _fake_evaluate_all_timeframes)
    prediction_module._prediction_cache.clear()

    client = _make_client()
    resp = client.get(f"/api/prediction/{symbol}", params={"timeframe": timeframe})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # spec'd top-level schema
    for key in (
        "symbol", "timeframe", "direction", "confidence", "probability_up",
        "probability_down", "current_price", "target", "stop",
        "prediction_horizon", "forecast_points", "upper_band_points",
        "lower_band_points", "reason",
    ):
        assert key in body, f"missing top-level '{key}'"
    assert body["prediction_horizon"]["bars"] == HORIZON_BARS[timeframe]

    if body["forecast"]["available"]:
        assert len(body["forecast_points"]) == HORIZON_BARS[timeframe] + 1
        assert len(body["upper_band_points"]) == len(body["lower_band_points"]) == len(body["forecast_points"])
        times = [p["time"] for p in body["forecast_points"]]
        assert times == sorted(times) and len(set(times)) == len(times)
        assert times[1] - times[0] == INTERVAL_MS[timeframe] // 1000
        assert body["forecast_diagnostics"]["timestamp_format"] == "unix_seconds"
        for series in ("forecast_points", "upper_band_points", "lower_band_points"):
            for p in body[series]:
                assert math.isfinite(p["price"]) and p["price"] > 0
        if body["direction"] == "NO_TRADE":
            assert body["forecast"]["trade_actionable"] is False
            assert body["forecast"]["target_price"] is None
            assert body["forecast"]["invalidation_price"] is None
    else:
        assert body["forecast_points"] == []
        assert body["prediction_horizon"]["reason"]

    # mirrored into the nested prediction object too
    assert body["prediction"]["forecast_points"] == body["forecast_points"]
