"""Server-side AI forecast / confidence-band point generation.

Pure functions: given the last closed candle, the prediction's levels and
the measured volatility, produce the exact point series the chart draws -
so every client renders the same cone, the horizon is timeframe-aware,
and the generation rules are unit-testable instead of living in canvas
code.

Honesty rules:
  - NO_TRADE (or a missing direction) produces NO points, plus the reason.
  - Band width comes from measured volatility - ATR first, then realized
    volatility, then a last-resort fixed percentage - and the response
    says which basis was used, so "bands look narrow" is explainable.
  - Points are anchored to the last candle's close/time: the forecast
    starts where the actual price series ends, never from a synthetic
    price.
"""

from __future__ import annotations

import math

# Bars ahead per timeframe. Chosen so the projected wall-clock horizon
# lands inside the product spec's window for each timeframe:
#   1m->45m, 5m->3h, 15m->8h, 30m->18h, 1h->36h, 4h->5d, 1d->3wk, 1w->8wk
HORIZON_BARS = {
    "1m": 45,
    "5m": 36,
    "15m": 32,
    "30m": 36,
    "1h": 36,
    "4h": 30,
    "1d": 21,
    "1w": 8,
}
DEFAULT_BARS = 40

# Last-resort band half-width when neither ATR nor realized volatility is
# available (a cold-started symbol with <14 candles of history).
FALLBACK_BAND_PCT = 0.006


def _ease_out(t: float) -> float:
    return 1 - (1 - t) ** 2


def horizon_bars(interval: str) -> int:
    return HORIZON_BARS.get(interval, DEFAULT_BARS)


def _band_spread(price: float, atr: float | None, realized_volatility: float | None) -> tuple[float, str]:
    """Half-width of the confidence band at full horizon, and which
    measurement produced it."""
    if atr is not None and math.isfinite(atr) and atr > 0:
        return atr, "atr"
    if realized_volatility is not None and math.isfinite(realized_volatility) and realized_volatility > 0:
        # realized_volatility is a daily-ish fraction (rolling stdev of
        # 1-bar returns, annualization factor baked in upstream); scale to
        # price units. Clamp so a volatility spike can't produce an absurd
        # cone that dwarfs the price axis.
        return price * min(realized_volatility, 0.25), "realized_volatility"
    return price * FALLBACK_BAND_PCT, "fallback_pct"


def build_forecast(
    *,
    interval: str,
    interval_ms: int,
    last_candle_time: int,
    price: float,
    direction: str | None,
    confidence: float | None,
    target: float | None,
    stop: float | None,
    atr: float | None = None,
    realized_volatility: float | None = None,
) -> dict:
    """Returns {forecast_points, upper_band_points, lower_band_points,
    bars, horizon_ms, band_basis, reason}. Point shape: {time, price},
    strictly increasing times starting one interval after the last candle.

    For LONG and SHORT alike: forecast eases from the current price toward
    the target; the upper band stays above the forecast and the lower band
    below it for the full horizon."""
    bars = horizon_bars(interval)
    meta = {
        "bars": bars,
        "horizon_ms": bars * interval_ms,
        "band_basis": None,
        "reason": None,
    }

    directional = direction in ("LONG", "SHORT")
    if not directional:
        return {
            **meta,
            "forecast_points": [],
            "upper_band_points": [],
            "lower_band_points": [],
            "reason": "NO_TRADE - no qualifying signal, so no forecast is fabricated",
        }
    if not (isinstance(price, (int, float)) and math.isfinite(price) and price > 0):
        return {
            **meta,
            "forecast_points": [],
            "upper_band_points": [],
            "lower_band_points": [],
            "reason": f"No valid current price ({price!r}) to anchor a forecast",
        }

    conf = max(0.0, min(100.0, confidence if confidence is not None else 50.0))
    end = target if (target is not None and math.isfinite(target) and target > 0) else price
    stop_level = stop if (stop is not None and math.isfinite(stop) and stop > 0) else price

    spread, band_basis = _band_spread(price, atr, realized_volatility)
    meta["band_basis"] = band_basis

    # Higher confidence -> tighter cone. 1.5x spread at 0% confidence,
    # 0.5x at 100%.
    width = spread * 2 * (1.5 - conf / 100)
    hi_end = max(end, stop_level, price) + width
    lo_end = min(end, stop_level, price) - width

    forecast, upper, lower = [], [], []
    for i in range(1, bars + 1):
        e = _ease_out(i / bars)
        t = last_candle_time + i * interval_ms
        forecast.append({"time": t, "price": round(price + (end - price) * e, 8)})
        upper.append({"time": t, "price": round(price + (hi_end - price) * e, 8)})
        lower.append({"time": t, "price": round(max(0.0, price + (lo_end - price) * e), 8)})

    return {
        **meta,
        "forecast_points": forecast,
        "upper_band_points": upper,
        "lower_band_points": lower,
    }
