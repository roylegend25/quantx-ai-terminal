"""Bybit V5 (Unified Trading Account) read-only adapter.

Only ever issues GET requests against documented read endpoints. No order,
transfer or withdrawal endpoint is ever called.
"""
import hashlib
import hmac
import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from app.exchanges.base import ExchangeAdapter, PermissionCheck

BASE = "https://api.bybit.com"
RECV_WINDOW = "5000"


class BybitAdapter(ExchangeAdapter):
    name = "bybit"

    def __init__(self):
        super().__init__()
        self._api_key = os.getenv("BYBIT_API_KEY", "")
        self._api_secret = os.getenv("BYBIT_API_SECRET", "")

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def _sign(self, timestamp: str, query: str) -> str:
        payload = f"{timestamp}{self._api_key}{RECV_WINDOW}{query}"
        return hmac.new(self._api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    async def _signed_get(self, path: str, params: dict | None = None) -> Any:
        self._require_configured()
        params = params or {}
        query = urlencode(sorted(params.items()))
        timestamp = str(int(time.time() * 1000))
        signature = self._sign(timestamp, query)
        headers = {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{BASE}{path}", params=params, headers=headers)
            r.raise_for_status()
            body = r.json()
            if body.get("retCode") != 0:
                raise RuntimeError(f"Bybit error {body.get('retCode')}: {body.get('retMsg')}")
            return body["result"]

    async def _public_get(self, path: str, params: dict | None = None) -> Any:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{BASE}{path}", params=params or {})
            r.raise_for_status()
            body = r.json()
            if body.get("retCode") != 0:
                raise RuntimeError(f"Bybit error {body.get('retCode')}: {body.get('retMsg')}")
            return body["result"]

    async def check_permissions(self) -> PermissionCheck:
        if not self.configured:
            return PermissionCheck(detectable=False, note="No API credentials configured")
        try:
            data = await self._signed_get("/v5/user/query-api")
        except Exception as e:
            return PermissionCheck(detectable=False, note=f"Permission check failed: {e!r}")

        wallet_perms = (data.get("permissions") or {}).get("Wallet") or []
        return PermissionCheck(
            detectable=True,
            withdraw_enabled="Withdraw" in wallet_perms,
            trade_enabled=not data.get("readOnly", True),
            read_enabled=True,
            raw=data,
        )

    async def get_account_info(self) -> dict:
        return await self._signed_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})

    async def get_balances(self) -> list[dict]:
        data = await self._signed_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        balances = []
        for acct in data.get("list", []):
            for coin in acct.get("coin", []):
                balances.append(
                    {
                        "asset": coin["coin"],
                        "balance": float(coin.get("walletBalance") or 0),
                        "available": float(coin.get("availableToWithdraw") or coin.get("walletBalance") or 0),
                    }
                )
        return balances

    async def get_positions(self) -> list[dict]:
        data = await self._signed_get("/v5/position/list", {"category": "linear", "settleCoin": "USDT"})
        return [
            {
                "symbol": p["symbol"],
                "side": p["side"] or "FLAT",
                "size": float(p["size"] or 0),
                "entry_price": float(p["avgPrice"] or 0),
                "mark_price": float(p["markPrice"] or 0),
                "unrealized_pnl": float(p["unrealisedPnl"] or 0),
                "leverage": float(p["leverage"] or 0),
            }
            for p in data.get("list", [])
            if float(p["size"] or 0) != 0
        ]

    async def get_open_orders(self) -> list[dict]:
        data = await self._signed_get("/v5/order/realtime", {"category": "linear", "settleCoin": "USDT"})
        return [
            {
                "order_id": o["orderId"],
                "symbol": o["symbol"],
                "side": o["side"],
                "type": o["orderType"],
                "price": float(o["price"] or 0),
                "qty": float(o["qty"] or 0),
                "status": o["orderStatus"],
            }
            for o in data.get("list", [])
        ]

    async def get_funding_rate(self, symbol: str) -> dict:
        data = await self._public_get("/v5/market/tickers", {"category": "linear", "symbol": symbol.upper()})
        rows = data.get("list", [])
        if not rows:
            return {"symbol": symbol.upper(), "funding_rate": None}
        row = rows[0]
        return {
            "symbol": row["symbol"],
            "funding_rate": float(row.get("fundingRate") or 0),
            "mark_price": float(row.get("markPrice") or 0),
        }

    async def get_open_interest(self, symbol: str) -> dict:
        data = await self._public_get(
            "/v5/market/open-interest",
            {"category": "linear", "symbol": symbol.upper(), "intervalTime": "5min"},
        )
        rows = data.get("list", [])
        latest = rows[0] if rows else {}
        return {"symbol": symbol.upper(), "open_interest": float(latest.get("openInterest") or 0)}

    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        data = await self._public_get(
            "/v5/market/orderbook", {"category": "linear", "symbol": symbol.upper(), "limit": limit}
        )
        return {
            "bids": [[float(p), float(q)] for p, q in data.get("b", [])],
            "asks": [[float(p), float(q)] for p, q in data.get("a", [])],
        }

    async def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        data = await self._public_get(
            "/v5/market/recent-trade", {"category": "linear", "symbol": symbol.upper(), "limit": limit}
        )
        return [
            {"price": float(t["price"]), "qty": float(t["size"]), "time": int(t["time"]), "side": t.get("side")}
            for t in data.get("list", [])
        ]

    async def get_candles(self, symbol: str, interval: str = "5m", limit: int = 100) -> list[dict]:
        interval_map = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
        data = await self._public_get(
            "/v5/market/kline",
            {
                "category": "linear",
                "symbol": symbol.upper(),
                "interval": interval_map.get(interval, "5"),
                "limit": limit,
            },
        )
        return [
            {
                "time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            for k in reversed(data.get("list", []))
        ]

    async def get_exchange_status(self) -> dict:
        try:
            await self._public_get("/v5/market/time")
            return {"exchange": self.name, "status": "operational", "reachable": True}
        except Exception as e:
            return {"exchange": self.name, "status": "unreachable", "reachable": False, "error": repr(e)}
