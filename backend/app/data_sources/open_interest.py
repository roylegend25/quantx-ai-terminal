"""Open-interest data: current snapshot + normalized history rows ready for
the open_interest_history table."""

from app.data_sources import binance_futures, coinglass
from app.data_sources.cache import cache
from app.data_sources.normalizer import nearest_oi_period, normalize_open_interest_hist


async def current(symbol: str) -> dict:
    async def _fetch():
        data = await binance_futures.fetch_open_interest(symbol)
        return {
            "symbol": symbol.upper(),
            "open_interest": float(data["openInterest"]),
            "provider": "binance_futures",
        }

    value, is_stale = await cache.get_or_fetch(f"oi:{symbol.upper()}", _fetch, ttl_seconds=60)
    return {**value, "stale": is_stale}


async def history(
    symbol: str,
    timeframe: str = "5m",
    limit: int = 500,
    start_ms: int | None = None,
    end_ms: int | None = None,
    provider: str = "binance_futures",
) -> tuple[list[dict], str]:
    """Normalized rows plus the sampling period actually used (Binance only
    serves 5m..1d periods, so e.g. 1m requests are served at 5m)."""
    period = nearest_oi_period(timeframe)

    if provider == "coinglass":
        if not coinglass.available():
            raise coinglass.ProviderUnavailable("COINGLASS_API_KEY not configured")
        rows = await coinglass.fetch_open_interest_history(symbol, interval=period, limit=limit)
        return (
            [{"time": r["time"], "open_interest": r["open_interest_value"], "open_interest_value": r["open_interest_value"]} for r in rows],
            period,
        )

    raw = await binance_futures.fetch_open_interest_hist(
        symbol, period=period, limit=limit, start_ms=start_ms, end_ms=end_ms
    )
    return normalize_open_interest_hist(raw), period
