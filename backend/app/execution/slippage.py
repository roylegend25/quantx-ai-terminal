"""Pre-trade expected slippage estimation and post-trade actual slippage
measurement, both derived from order-book depth. This module only cares
about execution quality - it never influences trade direction or sizing.
"""
from dataclasses import dataclass


@dataclass
class SlippageEstimate:
    expected_fill_price: float
    expected_slippage_bps: float
    reference_price: float
    depth_available: float
    depth_sufficient: bool


def walk_book(levels: list[list[float]], qty: float) -> tuple[float, float]:
    """Walks [price, size] levels (best price first), filling up to qty.

    Returns (vwap_fill_price, filled_qty). filled_qty may be less than qty
    if the levels don't have enough depth.
    """
    remaining = qty
    notional = 0.0
    filled = 0.0
    for price, size in levels:
        if remaining <= 1e-12:
            break
        take = min(remaining, size)
        if take <= 0:
            continue
        notional += take * price
        filled += take
        remaining -= take

    if filled <= 0:
        return 0.0, 0.0
    return notional / filled, filled


def estimate_slippage(orderbook: dict, side: str, qty: float) -> SlippageEstimate:
    """side: LONG (buys, walks asks) or SHORT (sells, walks bids)."""
    levels = orderbook.get("asks" if side == "LONG" else "bids") or []
    if not levels:
        raise ValueError("empty orderbook - cannot estimate slippage")

    reference_price = levels[0][0]
    vwap, filled = walk_book(levels, qty)
    if filled <= 0:
        vwap = reference_price

    depth_sufficient = filled >= qty - 1e-9
    slippage_bps = abs((vwap - reference_price) / reference_price) * 10_000

    return SlippageEstimate(
        expected_fill_price=round(vwap, 8),
        expected_slippage_bps=round(slippage_bps, 4),
        reference_price=reference_price,
        depth_available=round(filled, 8),
        depth_sufficient=depth_sufficient,
    )


def actual_slippage_bps(reference_price: float, fill_price: float, side: str) -> float:
    """Positive = adverse (paid more than reference on a buy, received less
    than reference on a sell). Negative = favorable (price improvement)."""
    if not reference_price:
        return 0.0
    raw = ((fill_price - reference_price) / reference_price) * 10_000
    return round(raw if side == "LONG" else -raw, 4)


def execution_quality(slippage_bps: float, latency_ms: float, fill_ratio: float) -> str:
    """Simple composite score - good enough to rank/alert on, not a formal
    transaction-cost-analysis benchmark."""
    if fill_ratio < 0.999:
        return "PARTIAL"
    if slippage_bps <= 2 and latency_ms <= 500:
        return "EXCELLENT"
    if slippage_bps <= 5 and latency_ms <= 1500:
        return "GOOD"
    if slippage_bps <= 15 and latency_ms <= 4000:
        return "FAIR"
    return "POOR"
