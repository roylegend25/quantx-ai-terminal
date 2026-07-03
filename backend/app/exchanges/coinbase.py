"""Coinbase Advanced Trade read-only adapter.

Authenticates with a Coinbase Developer Platform (CDP) API key using a
short-lived ES256 JWT signed per request, per Coinbase's documented auth
scheme. Only read (GET) endpoints are ever called - there is no method on
this class that can place an order, transfer funds, or withdraw.
"""
import os
import secrets
import time
from typing import Any

import httpx
import jwt

from app.exchanges.base import ExchangeAdapter, PermissionCheck

BASE = "https://api.coinbase.com"
HOST = "api.coinbase.com"


class CoinbaseAdapter(ExchangeAdapter):
    name = "coinbase"

    def __init__(self):
        super().__init__()
        self._key_name = os.getenv("COINBASE_API_KEY_NAME", "")
        # CDP private keys are usually stored as a single env-var line with
        # literal "\n" escapes - restore real newlines for PEM parsing.
        self._private_key = os.getenv("COINBASE_API_PRIVATE_KEY", "").replace("\\n", "\n")

    @property
    def configured(self) -> bool:
        return bool(self._key_name and self._private_key)

    def _build_jwt(self, method: str, path: str) -> str:
        now = int(time.time())
        payload = {
            "sub": self._key_name,
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,
            "uri": f"{method} {HOST}{path}",
        }
        headers = {"kid": self._key_name, "nonce": secrets.token_hex(16)}
        return jwt.encode(payload, self._private_key, algorithm="ES256", headers=headers)

    async def _get(self, path: str, params: dict | None = None) -> Any:
        self._require_configured()
        token = self._build_jwt("GET", path)
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{BASE}{path}", params=params or {}, headers={"Authorization": f"Bearer {token}"}
            )
            r.raise_for_status()
            return r.json()

    async def check_permissions(self) -> PermissionCheck:
        if not self.configured:
            return PermissionCheck(detectable=False, note="No API credentials configured")
        try:
            data = await self._get("/api/v3/brokerage/key_permissions")
        except Exception as e:
            return PermissionCheck(detectable=False, note=f"Permission check failed: {e!r}")

        return PermissionCheck(
            detectable=True,
            withdraw_enabled=bool(data.get("can_transfer")),
            trade_enabled=bool(data.get("can_trade")),
            read_enabled=bool(data.get("can_view")),
            raw=data,
        )

    async def get_account_info(self) -> dict:
        data = await self._get("/api/v3/brokerage/accounts")
        accounts = data.get("accounts", [])
        return {"account_count": len(accounts), "accounts": accounts}

    async def get_balances(self) -> list[dict]:
        data = await self._get("/api/v3/brokerage/accounts")
        balances = []
        for a in data.get("accounts", []):
            available = float(a.get("available_balance", {}).get("value") or 0)
            if available > 0:
                balances.append({"asset": a["currency"], "balance": available, "available": available})
        return balances

    async def get_positions(self) -> list[dict]:
        # Coinbase Advanced Trade's default portfolio is spot-only - there is
        # no perpetual/margin position concept to report here.
        return []

    async def get_open_orders(self) -> list[dict]:
        data = await self._get("/api/v3/brokerage/orders/historical/batch", {"order_status": "OPEN"})
        return [
            {
                "order_id": o["order_id"],
                "symbol": o["product_id"],
                "side": o["side"],
                "type": o.get("order_type"),
                "status": o["status"],
            }
            for o in data.get("orders", [])
        ]

    async def get_funding_rate(self, symbol: str) -> dict:
        return {"symbol": symbol.upper(), "funding_rate": None, "note": "Spot product - no funding rate"}

    async def get_open_interest(self, symbol: str) -> dict:
        return {"symbol": symbol.upper(), "open_interest": None, "note": "Spot product - no open interest"}

    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        data = await self._get("/api/v3/brokerage/product_book", {"product_id": symbol.upper(), "limit": limit})
        book = data.get("pricebook", {})
        return {
            "bids": [[float(b["price"]), float(b["size"])] for b in book.get("bids", [])],
            "asks": [[float(a["price"]), float(a["size"])] for a in book.get("asks", [])],
        }

    async def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        data = await self._get(f"/api/v3/brokerage/products/{symbol.upper()}/ticker", {"limit": limit})
        return [
            {"price": float(t["price"]), "qty": float(t["size"]), "time": t["time"], "side": t.get("side")}
            for t in data.get("trades", [])
        ]

    async def get_candles(self, symbol: str, interval: str = "5m", limit: int = 100) -> list[dict]:
        granularity_map = {
            "1m": "ONE_MINUTE",
            "5m": "FIVE_MINUTE",
            "15m": "FIFTEEN_MINUTE",
            "1h": "ONE_HOUR",
            "4h": "SIX_HOUR",
            "1d": "ONE_DAY",
        }
        granularity = granularity_map.get(interval, "FIVE_MINUTE")
        end = int(time.time())
        start = end - limit * 300
        data = await self._get(
            f"/api/v3/brokerage/products/{symbol.upper()}/candles",
            {"start": start, "end": end, "granularity": granularity},
        )
        return [
            {
                "time": int(c["start"]) * 1000,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c["volume"]),
            }
            for c in data.get("candles", [])
        ]

    async def get_exchange_status(self) -> dict:
        try:
            await self._get("/api/v3/brokerage/time")
            return {"exchange": self.name, "status": "operational", "reachable": True}
        except Exception as e:
            return {"exchange": self.name, "status": "unreachable", "reachable": False, "error": repr(e)}
