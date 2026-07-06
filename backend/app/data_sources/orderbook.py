"""Orderbook snapshots with derived microstructure metrics (spread, depth,
imbalance) - the shape stored in orderbook_snapshots and consumed by the
feature engine's orderbook-imbalance feature."""

import time

from app.data_sources import binance_futures
from app.data_sources.cache import cache


def compute_metrics(depth: dict) -> dict:
    bids = [(float(p), float(q)) for p, q in depth.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in depth.get("asks", [])]

    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else None

    bid_depth_usd = sum(p * q for p, q in bids)
    ask_depth_usd = sum(p * q for p, q in asks)
    total = bid_depth_usd + ask_depth_usd

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_bps": round((best_ask - best_bid) / mid * 10000, 4) if mid else None,
        "bid_depth_usd": round(bid_depth_usd, 2),
        "ask_depth_usd": round(ask_depth_usd, 2),
        "imbalance": round((bid_depth_usd - ask_depth_usd) / total, 4) if total > 0 else None,
    }


async def snapshot(symbol: str, limit: int = 100) -> dict:
    async def _fetch():
        depth = await binance_futures.fetch_orderbook(symbol, limit=limit)
        metrics = compute_metrics(depth)
        return {
            "symbol": symbol.upper(),
            "time": int(depth.get("T") or depth.get("E") or time.time() * 1000),
            **metrics,
            "levels": {
                "bids": depth.get("bids", [])[:20],
                "asks": depth.get("asks", [])[:20],
            },
            "provider": "binance_futures",
        }

    value, is_stale = await cache.get_or_fetch(f"orderbook:{symbol.upper()}:{limit}", _fetch, ttl_seconds=10)
    return {**value, "stale": is_stale}
