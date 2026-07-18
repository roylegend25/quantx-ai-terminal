"""Liquidation heatmap estimation for the Dashboard's Liquidation Heatmap card.

No premium liquidation-density provider (Hyblock, Coinglass, ...) is
configured in app.core.config for this deployment, and Binance itself only
publishes forced liquidations in real time with no REST history (see
app/intelligence/liquidations.py) - so this reconstructs where liquidations
are likely clustered from signals that *are* available: open interest,
funding rate, order book depth, a recent-candle volume profile, ATR, and any
liquidations actually observed on Binance's forced-order stream.

Method: recent traded ($ volume) approximates where positions were opened
("entry" price). Each entry bucket is projected to a long and a short
liquidation price under a handful of common leverage tiers
(liq_price = entry * (1 -+ (1/leverage - maintenance_margin))), weighted by
how much volume traded there and how common that leverage tier is assumed to
be. The projected buckets are then scaled to sum to the symbol's real open
interest notional (rather than an arbitrary unit), lightly augmented with
resting order-book depth and any liquidations actually observed, so the
output is comparable across symbols and grounded in live exchange data
throughout - never synthetic/fake numbers.
"""

import asyncio
import time

import httpx
import pandas as pd

from app.core.config import settings
from app.intelligence import funding, liquidations, open_interest
from app.quant.indicators import atr as _atr


def _provider_status() -> dict:
    """Phase 34: honest entitlement check, never a hardcoded claim. A
    configured CoinGlass key flips `coinglass_entitled` so the UI can show
    that an official-provider upgrade is possible; the actual CoinGlass
    HTTP client is intentionally not implemented here (no verified API
    contract/credentials exist in this deployment to build and test
    against) - wiring it in is the next step once a real subscription is
    available. Until then every response stays on the existing
    Binance-derived estimate and reports itself as such."""
    entitled = bool(settings.coinglass_api_key)
    return {
        "provider": "estimated",
        "coinglass_entitled": entitled,
        "provider_note": (
            "CoinGlass key detected but the official-provider client is not yet wired in - "
            "still serving the Binance-derived estimate."
        ) if entitled else "No CoinGlass subscription configured - serving a Binance-derived estimate.",
    }

BINANCE_FAPI = "https://fapi.binance.com"

NUM_BUCKETS = 80
KLINE_INTERVAL = "15m"
KLINE_LIMIT = 192  # ~48h of 15m candles - enough range for a stable volume profile
DEPTH_LIMIT = 100
LIQUIDATION_LISTEN_SECONDS = 1.2

MAINTENANCE_MARGIN = 0.004
# (leverage, assumed share of open interest sitting at that leverage) -
# skewed toward the 10x-25x band Binance retail perp traders cluster around
LEVERAGE_TIERS = [
    (5, 0.10),
    (10, 0.22),
    (20, 0.28),
    (25, 0.18),
    (50, 0.15),
    (100, 0.07),
]
ORDER_BOOK_WEIGHT = 0.06  # resting book depth folded in as a secondary signal, not the primary one

INTENSITY_LOW = 0.15
INTENSITY_MEDIUM = 0.4
INTENSITY_HIGH = 0.7


async def _fetch_klines(client: httpx.AsyncClient, symbol: str) -> list[list]:
    r = await client.get(
        f"{BINANCE_FAPI}/fapi/v1/klines",
        params={"symbol": symbol, "interval": KLINE_INTERVAL, "limit": KLINE_LIMIT},
    )
    r.raise_for_status()
    return r.json()


async def _fetch_depth(client: httpx.AsyncClient, symbol: str) -> dict:
    r = await client.get(
        f"{BINANCE_FAPI}/fapi/v1/depth",
        params={"symbol": symbol, "limit": DEPTH_LIMIT},
    )
    r.raise_for_status()
    return r.json()


def _atr_from_klines(klines: list[list]) -> float:
    if len(klines) < 15:
        return 0.0
    df = pd.DataFrame(
        {
            "high": [float(k[2]) for k in klines],
            "low": [float(k[3]) for k in klines],
            "close": [float(k[4]) for k in klines],
        }
    )
    value = _atr(df, period=14).iloc[-1]
    return float(value) if pd.notna(value) else float((df["high"] - df["low"]).mean())


def _leverage_zone_label(leverage: int) -> str:
    if leverage <= 5:
        return "1x-5x"
    if leverage <= 10:
        return "5x-10x"
    if leverage <= 20:
        return "10x-20x"
    if leverage <= 25:
        return "20x-25x"
    if leverage <= 50:
        return "25x-50x"
    return "50x-100x"


def _liq_fraction(leverage: int) -> float:
    return max(1.0 / leverage - MAINTENANCE_MARGIN, 0.0015)


def _intensity_label(intensity: float) -> str:
    if intensity >= INTENSITY_HIGH:
        return "EXTREME"
    if intensity >= INTENSITY_MEDIUM:
        return "HIGH"
    if intensity >= INTENSITY_LOW:
        return "MEDIUM"
    return "LOW"


def compute(
    symbol: str,
    klines: list[list],
    depth: dict,
    oi_data: dict,
    funding_data: dict,
    liquidation_data: dict,
    latency_ms: float | None = None,
) -> dict:
    current_price = float(funding_data.get("mark_price") or 0.0) or float(klines[-1][4])
    atr_value = _atr_from_klines(klines)

    half_width = max(atr_value * 12, current_price * 0.03)
    half_width = min(half_width, current_price * 0.15)
    price_low = current_price - half_width
    price_high = current_price + half_width
    bucket_size = (price_high - price_low) / NUM_BUCKETS if half_width > 0 else max(current_price * 0.001, 1e-6)

    bucket_prices = [price_low + (i + 0.5) * bucket_size for i in range(NUM_BUCKETS)]

    def bucket_index(price: float) -> int:
        idx = int((price - price_low) / bucket_size)
        return max(0, min(NUM_BUCKETS - 1, idx))

    # --- recent-candle $ volume profile stands in for "where positions were opened" ---
    entry_weight = [0.0] * NUM_BUCKETS
    for k in klines:
        high, low, close, quote_volume = float(k[2]), float(k[3]), float(k[4]), float(k[7])
        typical_price = (high + low + close) / 3
        if price_low <= typical_price <= price_high:
            entry_weight[bucket_index(typical_price)] += quote_volume
    total_weight = sum(entry_weight)
    entry_weight = (
        [w / total_weight for w in entry_weight] if total_weight > 0 else [1.0 / NUM_BUCKETS] * NUM_BUCKETS
    )

    # --- project each entry bucket to long/short liquidation prices per leverage tier ---
    long_weight = [0.0] * NUM_BUCKETS
    short_weight = [0.0] * NUM_BUCKETS
    long_tier_weight = [[0.0] * len(LEVERAGE_TIERS) for _ in range(NUM_BUCKETS)]
    short_tier_weight = [[0.0] * len(LEVERAGE_TIERS) for _ in range(NUM_BUCKETS)]

    for tier_i, (leverage, tier_share) in enumerate(LEVERAGE_TIERS):
        frac = _liq_fraction(leverage)
        for i, entry_price in enumerate(bucket_prices):
            w = entry_weight[i] * tier_share
            if w <= 0:
                continue
            long_price = entry_price * (1 - frac)
            short_price = entry_price * (1 + frac)
            if price_low <= long_price <= price_high:
                b = bucket_index(long_price)
                long_weight[b] += w
                long_tier_weight[b][tier_i] += w
            if price_low <= short_price <= price_high:
                b = bucket_index(short_price)
                short_weight[b] += w
                short_tier_weight[b][tier_i] += w

    # --- scale the (unitless) projection into $ notional grounded in real open interest ---
    oi_notional = float(oi_data.get("open_interest") or 0.0) * current_price
    funding_rate = float(funding_data.get("funding_rate") or 0.0)
    # positive funding = longs paying shorts = crowd leaning long = more long OI to liquidate
    skew = max(-1.0, min(1.0, funding_rate / 0.0015)) * 0.15
    long_share = max(0.2, min(0.8, 0.5 + skew))
    short_share = 1.0 - long_share

    total_long_weight = sum(long_weight) or 1.0
    total_short_weight = sum(short_weight) or 1.0
    long_notional = [w / total_long_weight * oi_notional * long_share for w in long_weight]
    short_notional = [w / total_short_weight * oi_notional * short_share for w in short_weight]

    # --- fold in resting order-book depth as a secondary signal ---
    for price_str, qty_str in depth.get("bids", []):
        price, qty = float(price_str), float(qty_str)
        if price_low <= price <= price_high:
            long_notional[bucket_index(price)] += price * qty * ORDER_BOOK_WEIGHT
    for price_str, qty_str in depth.get("asks", []):
        price, qty = float(price_str), float(qty_str)
        if price_low <= price <= price_high:
            short_notional[bucket_index(price)] += price * qty * ORDER_BOOK_WEIGHT

    # --- fold in any liquidations actually observed on Binance's forced-order stream ---
    liq_notional = float(liquidation_data.get("liquidation_notional") or 0.0)
    liq_pressure = liquidation_data.get("liquidation_pressure", "NEUTRAL")
    current_bucket = bucket_index(current_price)
    if liq_notional > 0:
        if liq_pressure == "LONG_LIQUIDATIONS":
            long_notional[current_bucket] += liq_notional
        elif liq_pressure == "SHORT_LIQUIDATIONS":
            short_notional[current_bucket] += liq_notional

    combined = [long_notional[i] + short_notional[i] for i in range(NUM_BUCKETS)]
    max_notional = max(combined) or 1.0

    clusters = []
    for i in range(NUM_BUCKETS):
        intensity = round(combined[i] / max_notional, 4)
        dominant_side = (
            "LONG" if long_notional[i] > short_notional[i] else ("SHORT" if short_notional[i] > long_notional[i] else "BALANCED")
        )
        tier_pool = long_tier_weight[i] if dominant_side != "SHORT" else short_tier_weight[i]
        dominant_leverage = LEVERAGE_TIERS[tier_pool.index(max(tier_pool))][0] if any(tier_pool) else None
        clusters.append(
            {
                "price": round(bucket_prices[i], 4),
                "long_notional": round(long_notional[i], 2),
                "short_notional": round(short_notional[i], 2),
                "intensity": intensity,
                "strength": _intensity_label(intensity),
                "dominant_side": dominant_side,
                "leverage_zone": _leverage_zone_label(dominant_leverage) if dominant_leverage else None,
            }
        )

    largest_long = max(clusters, key=lambda c: c["long_notional"])
    largest_short = max(clusters, key=lambda c: c["short_notional"])
    largest_cluster = max(clusters, key=lambda c: c["long_notional"] + c["short_notional"])

    below = [c for c in clusters if c["price"] < current_price and c["long_notional"] > 0]
    above = [c for c in clusters if c["price"] > current_price and c["short_notional"] > 0]
    nearest_long = max(below, key=lambda c: c["price"]) if below else None
    nearest_short = min(above, key=lambda c: c["price"]) if above else None

    total_long, total_short = sum(long_notional), sum(short_notional)
    imbalance_pct = (
        round((total_long - total_short) / (total_long + total_short) * 100, 2) if (total_long + total_short) > 0 else 0.0
    )

    # magnet: the cluster with the strongest pull toward it - size discounted by how far away it is
    def _pull(c: dict) -> float:
        distance_buckets = abs(c["price"] - current_price) / bucket_size
        return (c["long_notional"] + c["short_notional"]) / (1 + distance_buckets)

    magnet_cluster = max(clusters, key=_pull)

    # stop-hunt zone: the nearest at-least-medium-intensity cluster to the current price -
    # the level a sharp wick is most likely to sweep before reversing
    hunt_candidates = sorted(
        (c for c in clusters if c["intensity"] >= INTENSITY_MEDIUM and round(c["price"], 4) != round(current_price, 4)),
        key=lambda c: abs(c["price"] - current_price),
    )
    stop_hunt = hunt_candidates[0] if hunt_candidates else largest_cluster

    concentration = largest_cluster["intensity"]  # how peaked the single largest cluster is, 0..1
    funding_extremity = min(abs(funding_rate) / 0.001, 1.0)
    liquidation_flag = 1.0 if liq_notional > 0 else 0.0
    risk_score = round(
        min(100.0, concentration * 45 + abs(imbalance_pct) / 100 * 30 + funding_extremity * 15 + liquidation_flag * 10), 1
    )

    return {
        "symbol": symbol,
        "generated_at": time.time(),
        "current_price": round(current_price, 4),
        "atr": round(atr_value, 4),
        "price_low": round(price_low, 4),
        "price_high": round(price_high, 4),
        "bucket_size": round(bucket_size, 6),
        "clusters": clusters,
        "largest_long_cluster": largest_long,
        "largest_short_cluster": largest_short,
        "largest_cluster": largest_cluster,
        "nearest_long_cluster": nearest_long,
        "nearest_short_cluster": nearest_short,
        "liquidity_imbalance_pct": imbalance_pct,
        "magnet_price": magnet_cluster["price"],
        "stop_hunt_zone": {
            "price": stop_hunt["price"],
            "side": stop_hunt["dominant_side"],
            "strength": stop_hunt["strength"],
        },
        "risk_score": risk_score,
        "funding_rate": funding_rate,
        "open_interest_notional": round(oi_notional, 2),
        "recent_liquidations": {
            "count": liquidation_data.get("liquidation_count", 0),
            "notional": liq_notional,
            "pressure": liq_pressure,
        },
        "data_source": "binance_estimated",
        "source": "binance_estimated",
        "source_type": "derived_estimate",
        "market_timestamp": time.time(),
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        "stale": False,
        "fallback_source": None,
        **_provider_status(),
    }


def degraded(symbol: str, error: str) -> dict:
    """Every upstream Binance call failed (network outage, rate limit, ...) -
    still returns a structurally complete, clearly-labeled response so the
    card can render a "data unavailable" state instead of going blank."""
    return {
        "symbol": symbol,
        "generated_at": time.time(),
        "current_price": None,
        "atr": None,
        "price_low": None,
        "price_high": None,
        "bucket_size": None,
        "clusters": [],
        "largest_long_cluster": None,
        "largest_short_cluster": None,
        "largest_cluster": None,
        "nearest_long_cluster": None,
        "nearest_short_cluster": None,
        "liquidity_imbalance_pct": None,
        "magnet_price": None,
        "stop_hunt_zone": None,
        "risk_score": None,
        "funding_rate": None,
        "open_interest_notional": None,
        "recent_liquidations": {"count": 0, "notional": 0.0, "pressure": "NEUTRAL"},
        "data_source": "unavailable",
        "source": None,
        "source_type": None,
        "latency_ms": None,
        "stale": False,
        "fallback_source": None,
        "error": error,
        "provider": None,
        "coinglass_entitled": bool(settings.coinglass_api_key),
        "provider_note": "No data source is currently reachable.",
    }


async def build(symbol: str) -> dict:
    symbol = symbol.upper()
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=10) as client:
        klines, depth, oi_data, funding_data, liquidation_data = await asyncio.gather(
            _fetch_klines(client, symbol),
            _fetch_depth(client, symbol),
            open_interest.fetch(symbol),
            funding.fetch(symbol),
            liquidations.fetch(symbol, listen_seconds=LIQUIDATION_LISTEN_SECONDS),
        )
    latency_ms = (time.monotonic() - started) * 1000
    return compute(symbol, klines, depth, oi_data, funding_data, liquidation_data, latency_ms=latency_ms)
