"""Canonical shapes for everything the data engine stores.

Every provider fetcher returns raw payloads; this module turns them into the
one dict-per-row format the validator/interpolator/storage layer works with,
and owns the supported symbol/timeframe universe so the API, downloader and
feature engine never disagree about what "15m" means in milliseconds.
"""
from app.timeframes.canonical import TIMEFRAME_CAPABILITIES, parse_timeframe

MINUTE_MS = 60_000
HOUR_MS = 60 * MINUTE_MS
DAY_MS = 24 * HOUR_MS

# Every timeframe the data engine can download, clean, store and feature-ize.
TIMEFRAMES_MS: dict[str, int] = {
    value: metadata.fixed_duration_ms for value, metadata in TIMEFRAME_CAPABILITIES.items()
    if metadata.fixed_duration_ms is not None
}

SUPPORTED_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

# Binance's openInterestHist endpoint only serves these sampling periods -
# other timeframes fall back to the nearest supported one.
OI_PERIODS = ["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]


def timeframe_ms(timeframe: str) -> int:
    canonical = parse_timeframe(timeframe).value
    if canonical == "1M":
        raise ValueError("Calendar timeframe '1M' has no fixed millisecond duration")
    if canonical not in TIMEFRAMES_MS:
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Supported: {', '.join(TIMEFRAMES_MS)}")
    return TIMEFRAMES_MS[canonical]


def nearest_oi_period(timeframe: str) -> str:
    tf = parse_timeframe(timeframe).value
    if tf in OI_PERIODS:
        return tf
    target = timeframe_ms(tf)
    return min(OI_PERIODS, key=lambda p: abs(TIMEFRAMES_MS[p] - target))


def normalize_klines(raw: list[list]) -> list[dict]:
    """Binance kline array -> canonical candle dicts (both spot and futures
    use the same array layout)."""
    rows = []
    for k in raw:
        rows.append(
            {
                "time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "quote_volume": float(k[7]) if len(k) > 7 else None,
                "trades_count": int(k[8]) if len(k) > 8 else None,
            }
        )
    return rows


def normalize_funding_history(raw: list[dict]) -> list[dict]:
    return [
        {
            "time": int(r["fundingTime"]),
            "funding_rate": float(r["fundingRate"]),
            "mark_price": float(r["markPrice"]) if r.get("markPrice") not in (None, "") else None,
        }
        for r in raw
    ]


def normalize_open_interest_hist(raw: list[dict]) -> list[dict]:
    return [
        {
            "time": int(r["timestamp"]),
            "open_interest": float(r["sumOpenInterest"]),
            "open_interest_value": float(r["sumOpenInterestValue"]) if r.get("sumOpenInterestValue") else None,
        }
        for r in raw
    ]


def normalize_agg_trades(raw: list[dict]) -> list[dict]:
    return [
        {
            "time": int(r["T"]),
            "trade_id": int(r["a"]),
            "price": float(r["p"]),
            "qty": float(r["q"]),
            # m=True means the buyer is the maker, i.e. the taker sold
            "side": "SELL" if r.get("m") else "BUY",
        }
        for r in raw
    ]
