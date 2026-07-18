"""Hyperliquid BTC/ETH large-trades feed for the dashboard's Hyperliquid tab.

Hyperliquid's public info endpoint has no REST history for the market-wide
trade tape (only a per-user fills endpoint, which needs a wallet address and
isn't what a market-wide "large trades" view needs) - trades are only
published live over the public WebSocket. This mirrors the same short
listen-window pattern already used for Binance forced liquidations in
app/intelligence/liquidations.py: connect, listen for a few seconds, close.
Every field below is read directly off Hyperliquid's own message - nothing
here is estimated, synthesized, or scraped.

Verified against Hyperliquid's official docs (for-developers/api/websocket):
  - wss://api.hyperliquid.xyz/ws (mainnet), wss://api.hyperliquid-testnet.xyz/ws (testnet)
  - subscribe: {"method": "subscribe", "subscription": {"type": "trades", "coin": "<coin>"}}
  - trade messages: {"channel": "trades", "data": [WsTrade, ...]}
  - WsTrade: {coin, side, px, sz, hash, time, tid, users: [buyer, seller]}
  - side is "B" (buyer was the aggressor) or "A" (seller was the aggressor),
    per the official Python SDK's Side = Literal["A"] | Literal["B"].
"""

import asyncio
import json
import time

import websockets

HYPERLIQUID_WS = "wss://api.hyperliquid.xyz/ws"

SUPPORTED_COINS = ("BTC", "ETH")
DEFAULT_LISTEN_SECONDS = 2.5
CONNECT_TIMEOUT_SECONDS = 2.5
# hard ceiling on connect + listen combined - this endpoint must never make
# a dashboard poll hang waiting on an external exchange
HARD_BUDGET_SECONDS = 5.0
DEFAULT_MIN_NOTIONAL = 50_000.0

_SIDE_LABEL = {"B": "BUY", "A": "SELL"}


def _normalize(raw: dict) -> dict | None:
    try:
        coin = raw["coin"]
        px = float(raw["px"])
        sz = float(raw["sz"])
        side = raw.get("side")
        ts_ms = int(raw["time"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "coin": coin,
        "side": _SIDE_LABEL.get(side, side),
        "price": px,
        "size": sz,
        "notional": round(px * sz, 2),
        "time": ts_ms,
        "trade_id": raw.get("tid"),
        "hash": raw.get("hash"),
    }


async def _listen(coins: tuple[str, ...], listen_seconds: float) -> list[dict]:
    trades: list[dict] = []
    async with websockets.connect(HYPERLIQUID_WS, open_timeout=CONNECT_TIMEOUT_SECONDS) as ws:
        for coin in coins:
            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": coin}}))

        deadline = asyncio.get_event_loop().time() + listen_seconds
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("channel") != "trades":
                continue
            for item in msg.get("data") or []:
                normalized = _normalize(item)
                if normalized is not None:
                    trades.append(normalized)
    return trades


async def fetch(
    coins: tuple[str, ...] = SUPPORTED_COINS,
    listen_seconds: float = DEFAULT_LISTEN_SECONDS,
    min_notional: float = DEFAULT_MIN_NOTIONAL,
) -> dict:
    """Samples a short live window of the Hyperliquid public trade tape and
    returns only prints at/above min_notional. An empty result after a
    successful listen is a real "no large trades in this window" outcome,
    never treated as an error; only a genuine connection/protocol failure
    sets data_source to "unavailable"."""
    listen_seconds = max(0.5, min(listen_seconds, HARD_BUDGET_SECONDS - CONNECT_TIMEOUT_SECONDS))
    fetched_at = time.time()
    started = time.monotonic()
    try:
        raw_trades = await asyncio.wait_for(_listen(coins, listen_seconds), timeout=HARD_BUDGET_SECONDS)
    except Exception as exc:
        return {
            "trades": [],
            "coins": list(coins),
            "min_notional": min_notional,
            "sample_window_seconds": listen_seconds,
            "fetched_at": fetched_at,
            "data_source": "unavailable",
            "source": None,
            "source_type": None,
            "market_timestamp": None,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "stale": False,
            "fallback_source": None,
            "error": str(exc),
        }

    large = sorted(
        (t for t in raw_trades if t["notional"] >= min_notional),
        key=lambda t: t["time"],
        reverse=True,
    )
    latest_ms = max((t["time"] for t in large), default=None)
    return {
        "trades": large,
        "coins": list(coins),
        "min_notional": min_notional,
        "sample_window_seconds": listen_seconds,
        "fetched_at": fetched_at,
        "data_source": "hyperliquid_ws",
        "source": "hyperliquid_ws",
        "source_type": "exchange_ws",
        "market_timestamp": (latest_ms / 1000) if latest_ms is not None else None,
        "latency_ms": round((time.monotonic() - started) * 1000, 1),
        "stale": False,
        "fallback_source": None,
        "error": None,
    }
