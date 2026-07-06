"""Liquidation data.

Two tiers, clearly labeled and never mixed up:
- Coinglass measured liquidation history when COINGLASS_API_KEY is set
  (method="coinglass").
- Otherwise honest *estimates* derived from real stored data - bars where
  open interest dropped sharply while price wicked hard are flagged as
  probable liquidation events, with notional estimated from the OI change
  (method="oi_wick_estimate"). No fabricated numbers: every input is a real
  candle/OI row already downloaded, and the method column says it's derived.
"""

from app.data_sources import coinglass

# An OI drop of this fraction within one sampling bar, combined with a wick,
# is treated as a probable liquidation flush.
OI_DROP_THRESHOLD = 0.005
WICK_BODY_RATIO = 1.0


async def coinglass_history(symbol: str, interval: str = "1h", limit: int = 500) -> list[dict]:
    rows = await coinglass.fetch_liquidation_history(symbol, interval=interval, limit=limit)
    out = []
    for r in rows:
        if r["long_liquidation_usd"]:
            out.append(
                {
                    "time": r["time"],
                    "side": "LONG",
                    "price_level": None,
                    "notional_usd": r["long_liquidation_usd"],
                    "strength": None,
                    "method": "coinglass",
                }
            )
        if r["short_liquidation_usd"]:
            out.append(
                {
                    "time": r["time"],
                    "side": "SHORT",
                    "price_level": None,
                    "notional_usd": r["short_liquidation_usd"],
                    "strength": None,
                    "method": "coinglass",
                }
            )
    return out


def estimate_from_candles_and_oi(candles: list[dict], oi_rows: list[dict]) -> list[dict]:
    """Cross-reference stored candles with stored OI history: a bar where OI
    fell more than OI_DROP_THRESHOLD while price left a wick larger than the
    body is scored as a liquidation estimate on the wick's side."""
    oi_by_time = {r["time"]: r["open_interest"] for r in oi_rows}
    oi_times = sorted(oi_by_time)
    if len(oi_times) < 2 or not candles:
        return []

    def oi_at(ts: int) -> float | None:
        # last OI sample at or before ts
        lo, hi = 0, len(oi_times) - 1
        if ts < oi_times[0]:
            return None
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if oi_times[mid] <= ts:
                best = oi_times[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        return oi_by_time[best] if best is not None else None

    events = []
    notionals = []
    raw = []
    for prev, cur in zip(candles, candles[1:]):
        oi_prev, oi_cur = oi_at(prev["time"]), oi_at(cur["time"])
        if not oi_prev or not oi_cur or oi_prev <= 0:
            continue
        oi_drop = (oi_prev - oi_cur) / oi_prev
        if oi_drop < OI_DROP_THRESHOLD:
            continue

        body = abs(cur["close"] - cur["open"])
        lower_wick = min(cur["open"], cur["close"]) - cur["low"]
        upper_wick = cur["high"] - max(cur["open"], cur["close"])
        if max(lower_wick, upper_wick) < body * WICK_BODY_RATIO:
            continue

        side = "LONG" if lower_wick >= upper_wick else "SHORT"
        price_level = cur["low"] if side == "LONG" else cur["high"]
        notional = (oi_prev - oi_cur) * cur["close"]
        raw.append({"time": cur["time"], "side": side, "price_level": price_level, "notional_usd": round(notional, 2)})
        notionals.append(notional)

    max_notional = max(notionals) if notionals else 0.0
    for r in raw:
        events.append(
            {
                **r,
                "strength": round(r["notional_usd"] / max_notional * 100, 2) if max_notional > 0 else None,
                "method": "oi_wick_estimate",
            }
        )
    return events
