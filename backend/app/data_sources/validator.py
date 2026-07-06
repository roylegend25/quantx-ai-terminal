"""Candle-dataset validation: dedup, broken-OHLC rejection, gap detection,
zero-volume/stale/outlier flagging, and a 0-100 quality score.

The contract with the rest of the system: bad data is never silently used.
Rows that are structurally broken are dropped (and counted); everything else
is kept but *reported*, and the dataset-level quality score plus gap report
travel with the data so downstream consumers (backtester, feature engine,
learning loop) can refuse datasets below their threshold.
"""

import math
import time as _time

# Datasets scoring below this are considered unusable for backtesting or
# feature generation; the API surfaces the reason instead of proceeding.
MIN_USABLE_QUALITY = 40.0

# MAD-based robust z-score above this marks a bar's return as an outlier.
OUTLIER_MAD_Z = 8.0

# A candle stream whose newest bar is older than this many bar-widths is
# stale for live purposes (harmless for deep-history downloads).
STALE_AFTER_BARS = 3


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _detect_outliers(rows: list[dict]) -> list[int]:
    """Indices of rows whose log return is a robust (MAD) outlier."""
    if len(rows) < 20:
        return []
    returns = []
    for prev, cur in zip(rows, rows[1:]):
        if prev["close"] > 0 and cur["close"] > 0:
            returns.append(math.log(cur["close"] / prev["close"]))
        else:
            returns.append(0.0)
    med = _median(returns)
    abs_dev = [abs(r - med) for r in returns]
    mad = _median(abs_dev)
    if mad <= 0:
        return []
    out = []
    for i, r in enumerate(returns):
        z = 0.6745 * (r - med) / mad
        if abs(z) > OUTLIER_MAD_Z:
            out.append(i + 1)  # the row that produced the return
    return out


def _is_broken(row: dict) -> bool:
    try:
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    except (KeyError, TypeError, ValueError):
        return True
    if any(map(math.isnan, (o, h, l, c))) or min(o, h, l, c) <= 0:
        return True
    return h < l or h < max(o, c) - 1e-12 or l > min(o, c) + 1e-12


def find_gaps(rows: list[dict], timeframe_ms: int) -> list[dict]:
    gaps = []
    for prev, cur in zip(rows, rows[1:]):
        delta = cur["time"] - prev["time"]
        if delta > timeframe_ms * 1.5:
            missing = round(delta / timeframe_ms) - 1
            gaps.append({"start": prev["time"] + timeframe_ms, "end": cur["time"] - timeframe_ms, "bars": missing})
    return gaps


def validate_candles(rows: list[dict], timeframe_ms: int, now_ms: int | None = None) -> tuple[list[dict], dict]:
    """Returns (clean_rows, report). clean_rows are sorted, deduped and free
    of structurally broken bars; the report counts everything that was wrong
    plus the resulting gaps and a 0-100 quality score."""
    now_ms = now_ms or int(_time.time() * 1000)

    total_in = len(rows)
    by_time: dict[int, dict] = {}
    duplicates = 0
    broken = 0
    for row in sorted(rows, key=lambda r: r.get("time") or 0):
        ts = row.get("time")
        if ts is None:
            broken += 1
            continue
        if _is_broken(row):
            broken += 1
            continue
        if ts in by_time:
            duplicates += 1
        by_time[ts] = row  # keep the last occurrence

    clean = [by_time[t] for t in sorted(by_time)]

    zero_volume = sum(1 for r in clean if not float(r.get("volume") or 0.0))
    outlier_indices = _detect_outliers(clean)
    gaps = find_gaps(clean, timeframe_ms)
    missing = sum(g["bars"] for g in gaps)
    largest_gap = max((g["bars"] for g in gaps), default=0)

    expected = len(clean) + missing
    stale = bool(clean) and (now_ms - clean[-1]["time"]) > timeframe_ms * (STALE_AFTER_BARS + 1)

    score = 100.0
    if expected > 0:
        score -= min(40.0, missing / expected * 100 * 2)  # missing data hurts most
    if clean:
        score -= min(15.0, zero_volume / len(clean) * 100)
        score -= min(15.0, len(outlier_indices) / len(clean) * 100 * 3)
    if total_in > 0:
        score -= min(20.0, broken / total_in * 100 * 2)
    if largest_gap > 20:
        score -= 10.0
    if not clean:
        score = 0.0
    score = round(max(0.0, score), 2)

    report = {
        "rows_in": total_in,
        "rows": len(clean),
        "expected_rows": expected,
        "duplicates": duplicates,
        "broken_ohlc": broken,
        "zero_volume": zero_volume,
        "outliers": len(outlier_indices),
        "outlier_timestamps": [clean[i]["time"] for i in outlier_indices if i < len(clean)][:50],
        "missing_candles": missing,
        "gaps": gaps[:100],
        "largest_gap_bars": largest_gap,
        "stale": stale,
        "quality_score": score,
        "usable": score >= MIN_USABLE_QUALITY,
    }
    return clean, report
