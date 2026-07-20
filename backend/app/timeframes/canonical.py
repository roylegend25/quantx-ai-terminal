"""Single case-sensitive parser for exchange and UI timeframes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class Timeframe(str, Enum):
    M1="1m"; M3="3m"; M5="5m"; M15="15m"; M30="30m"
    H1="1h"; H2="2h"; H4="4h"; H6="6h"; H12="12h"
    D1="1d"; W1="1w"; MONTH1="1M"


_ALIASES = {"1W": Timeframe.W1}


@dataclass(frozen=True)
class TimeframeCapabilities:
    value: str
    kind: str
    fixed_duration_ms: int | None
    provider_interval: str
    storage_key: str
    chart_supported: bool = True
    data_api_supported: bool = True
    prediction_supported: bool = True
    execution_supported: bool = False
    validation_strategy: str = "fixed_duration"
    cache_policy: str = "timestamped_candles"


_FIXED_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "12h": 43_200_000, "1d": 86_400_000, "1w": 604_800_000,
}
_EXECUTION_TIMEFRAMES = {"1m", "5m", "15m", "1h"}
TIMEFRAME_CAPABILITIES: dict[str, TimeframeCapabilities] = {
    value: TimeframeCapabilities(
        value=value,
        kind="calendar" if value == "1M" else "fixed",
        fixed_duration_ms=_FIXED_MS.get(value),
        provider_interval=value,
        storage_key=value,
        execution_supported=value in _EXECUTION_TIMEFRAMES,
        validation_strategy="calendar_month" if value == "1M" else "weekly_boundary" if value == "1w" else "fixed_duration",
        cache_policy="calendar_boundary" if value == "1M" else "timestamped_candles",
    ) for value in (timeframe.value for timeframe in Timeframe)
}


def parse_timeframe(value: str | Timeframe) -> Timeframe:
    if isinstance(value, Timeframe):
        return value
    if not isinstance(value, str):
        raise ValueError("Timeframe must be a string")
    if value in _ALIASES:
        return _ALIASES[value]
    try:
        return Timeframe(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported timeframe '{value}'") from exc


def timeframe_capabilities(value: str | Timeframe) -> TimeframeCapabilities:
    return TIMEFRAME_CAPABILITIES[parse_timeframe(value).value]


def is_supported_data_timeframe(value: str | Timeframe) -> bool:
    try:
        return timeframe_capabilities(value).data_api_supported
    except ValueError:
        return False


def exchange_interval(value: str | Timeframe) -> str:
    return parse_timeframe(value).value


def to_provider_interval(value: str | Timeframe, provider: str) -> str:
    """Return the provider spelling, never a persistence-only alias."""
    if provider not in {"binance_futures", "binance_spot", "binance"}:
        raise ValueError(f"Unsupported timeframe provider '{provider}'")
    return timeframe_capabilities(value).provider_interval


def cache_key(value: str | Timeframe) -> str:
    return f"timeframe:{parse_timeframe(value).value}"


def storage_interval(value: str | Timeframe) -> str:
    return timeframe_capabilities(value).storage_key


def from_storage_interval(value: str) -> Timeframe:
    return parse_timeframe(value)


def month_boundary_ms(value_ms: int) -> int:
    value = datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc)
    return int(datetime(value.year, value.month, 1, tzinfo=timezone.utc).timestamp() * 1000)


def next_month_boundary_ms(value_ms: int) -> int:
    value = datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc)
    year, month = (value.year + 1, 1) if value.month == 12 else (value.year, value.month + 1)
    return int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1000)


def calendar_month_age(value_ms: int, now_ms: int) -> int:
    """Whole exchange-month boundaries crossed since a candle open."""
    cursor, age = month_boundary_ms(value_ms), 0
    current = month_boundary_ms(now_ms)
    while cursor < current:
        cursor = next_month_boundary_ms(cursor)
        age += 1
    return age


def calendar_month_boundaries(start_ms: int, end_ms: int) -> list[int]:
    cursor = month_boundary_ms(start_ms)
    if cursor < start_ms:
        cursor = next_month_boundary_ms(cursor)
    boundaries = []
    while cursor <= end_ms:
        boundaries.append(cursor)
        cursor = next_month_boundary_ms(cursor)
    return boundaries
