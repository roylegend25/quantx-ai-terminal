from datetime import datetime, timezone
import pytest

from app.quant.forecast import _advance_calendar_month, _forecast_timestamp, build_forecast, validate_forecast


def ts(year,month,day=1): return int(datetime(year,month,day,tzinfo=timezone.utc).timestamp())


def test_calendar_month_leap_year_clamping_and_year_transition():
    assert _advance_calendar_month(ts(2024,1,31))==ts(2024,2,29)
    assert _advance_calendar_month(ts(2023,1,31))==ts(2023,2,28)
    assert _advance_calendar_month(ts(2025,12,1))==ts(2026,1,1)


def test_one_minute_and_one_month_are_unambiguous():
    base=ts(2024,1,1)
    assert _forecast_timestamp(base,"1m",1)==base+60
    assert _forecast_timestamp(base,"1M",1)==ts(2024,2,1)
    monday=ts(2024,1,1)
    assert _forecast_timestamp(monday,"1w",1)==ts(2024,1,8)


def test_monthly_forecast_uses_actual_candle_boundaries_and_is_informational():
    forecast=build_forecast(interval="1M",interval_ms=30*86400000,last_candle_time=ts(2024,1,1)*1000,
        price=100,direction="NO_TRADE",confidence=None,target=None,stop=None,informational_direction="BULLISH",
        informational_strength=.5,candle_count=50)
    assert [p["time"] for p in forecast["median_path"]]==[ts(2024,1,1),ts(2024,2,1),ts(2024,3,1),ts(2024,4,1)]
    assert forecast["trade_actionable"] is False
    assert validate_forecast(forecast,reference_time=ts(2024,1,1),interval="1M")[0]


@pytest.mark.parametrize("direction",["LONG","SHORT","NO_TRADE"])
def test_monthly_direction_never_becomes_actionable(direction):
    forecast=build_forecast(interval="1M",interval_ms=30*86400000,last_candle_time=ts(2024,1,1)*1000,
        price=100,direction=direction,confidence=80,target=120,stop=90,
        informational_direction="NEUTRAL",informational_strength=.5,candle_count=50)
    assert forecast["actionable"] is False and forecast["execution_eligible"] is False
    assert forecast["informational_only"] is True and forecast["entry"] is None
    assert forecast["stop_loss"] is None and forecast["order_instruction"] is None
    assert "MONTHLY_FORECAST_INFORMATIONAL_ONLY" in forecast["blockers"]
