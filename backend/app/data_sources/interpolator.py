"""Gap repair for validated candle datasets.

Only small gaps (<= MAX_INTERPOLATE_GAP_BARS) are filled, linearly between
the two real neighbors, and every synthetic bar is stamped
interpolated=True with zero volume so nothing downstream can mistake it for
market-observed data. Larger gaps are left open and reported as rejected -
never bridged - because inventing an hour of price action is worse than
admitting the hole.
"""

MAX_INTERPOLATE_GAP_BARS = 3


def fill_gaps(
    rows: list[dict],
    timeframe_ms: int,
    max_gap_bars: int = MAX_INTERPOLATE_GAP_BARS,
) -> tuple[list[dict], int, list[dict]]:
    """Returns (rows_with_fills, interpolated_count, gap_actions).
    gap_actions mirrors the validator's gap list with an "action" field:
    "interpolated" or "rejected"."""
    if len(rows) < 2:
        return rows, 0, []

    out: list[dict] = [rows[0]]
    interpolated = 0
    gap_actions: list[dict] = []

    for prev, cur in zip(rows, rows[1:]):
        delta = cur["time"] - prev["time"]
        missing = round(delta / timeframe_ms) - 1
        if missing >= 1:
            gap = {
                "start": prev["time"] + timeframe_ms,
                "end": cur["time"] - timeframe_ms,
                "bars": missing,
            }
            if missing <= max_gap_bars:
                for i in range(1, missing + 1):
                    frac = i / (missing + 1)
                    close = prev["close"] + (cur["close"] - prev["close"]) * frac
                    prev_close = out[-1]["close"]
                    out.append(
                        {
                            "time": prev["time"] + i * timeframe_ms,
                            "open": prev_close,
                            "high": max(prev_close, close),
                            "low": min(prev_close, close),
                            "close": close,
                            "volume": 0.0,
                            "quote_volume": None,
                            "trades_count": None,
                            "interpolated": True,
                        }
                    )
                    interpolated += 1
                gap_actions.append({**gap, "action": "interpolated"})
            else:
                gap_actions.append({**gap, "action": "rejected"})
        out.append(cur)

    return out, interpolated, gap_actions
