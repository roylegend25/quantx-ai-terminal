import pytest

from datetime import datetime, timezone

from app.data_sources.normalizer import timeframe_ms
from app.timeframes.canonical import (
    Timeframe,
    cache_key,
    calendar_month_boundaries,
    from_storage_interval,
    parse_timeframe,
    storage_interval,
    to_provider_interval,
)


def test_minute_month_week_are_unambiguous():
    assert parse_timeframe("1m") is Timeframe.M1
    assert parse_timeframe("1M") is Timeframe.MONTH1
    assert parse_timeframe("1w") is parse_timeframe("1W") is Timeframe.W1
    assert cache_key("1m") != cache_key("1M")
    with pytest.raises(ValueError, match="no fixed millisecond duration"):
        timeframe_ms("1M")
    assert storage_interval("1M") == "1M"
    assert from_storage_interval("1M") is Timeframe.MONTH1
    assert to_provider_interval("1M", "binance_futures") == "1M"


@pytest.mark.parametrize("invalid",["1mo","1Mo","1WEEK","1H","01m","month"])
def test_invalid_mixed_timeframes_are_rejected(invalid):
    with pytest.raises(ValueError): parse_timeframe(invalid)


def _ms(year, month, day=1):
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


def test_calendar_month_boundaries_cover_variable_months_and_year_transition():
    assert calendar_month_boundaries(_ms(2024, 1), _ms(2024, 4)) == [
        _ms(2024, 1), _ms(2024, 2), _ms(2024, 3), _ms(2024, 4)
    ]
    assert calendar_month_boundaries(_ms(2023, 12), _ms(2024, 2)) == [
        _ms(2023, 12), _ms(2024, 1), _ms(2024, 2)
    ]
    assert calendar_month_boundaries(_ms(2024, 1, 31), _ms(2024, 3)) == [_ms(2024, 2), _ms(2024, 3)]
