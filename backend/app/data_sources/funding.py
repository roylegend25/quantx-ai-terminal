"""Funding-rate data: current snapshot + normalized history rows ready for
the funding_rates table. Primary source is Binance futures (free); Coinglass
is used only as an extra provider when a key is configured."""

from app.data_sources import binance_futures, coinglass
from app.data_sources.cache import cache
from app.data_sources.normalizer import normalize_funding_history


async def current(symbol: str) -> dict:
    async def _fetch():
        data = await binance_futures.fetch_premium_index(symbol)
        return {
            "symbol": symbol.upper(),
            "funding_rate": float(data["lastFundingRate"]),
            "mark_price": float(data["markPrice"]),
            "index_price": float(data["indexPrice"]),
            "next_funding_time": data.get("nextFundingTime"),
            "provider": "binance_futures",
        }

    value, is_stale = await cache.get_or_fetch(f"funding:{symbol.upper()}", _fetch, ttl_seconds=60)
    return {**value, "stale": is_stale}


async def history(
    symbol: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = 1000,
    provider: str = "binance_futures",
) -> list[dict]:
    """Normalized rows: {"time", "funding_rate", "mark_price"}."""
    if provider == "coinglass":
        if not coinglass.available():
            raise coinglass.ProviderUnavailable("COINGLASS_API_KEY not configured")
        rows = await coinglass.fetch_funding_history(symbol, limit=limit)
        return [{"time": r["time"], "funding_rate": r["funding_rate"], "mark_price": None} for r in rows]

    raw = await binance_futures.fetch_funding_history(symbol, start_ms=start_ms, end_ms=end_ms, limit=limit)
    return normalize_funding_history(raw)
