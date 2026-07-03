"""Binance USDT-M Futures read-only adapter.

Only ever issues GET requests against documented read endpoints. Trading,
transfer and withdrawal endpoints are never called and have no method on
this class to call them through.
"""
import hashlib
import hmac
import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from app.exchanges.base import ExchangeAdapter, PermissionCheck

FUTURES_BASE = "https://fapi.binance.com"
SPOT_BASE = "https://api.binance.com"  # apiRestrictions only exists on the spot host


class BinanceAdapter(ExchangeAdapter):
    name = "binance"

    def __init__(self):
        super().__init__()
        self._api_key = os.getenv("BINANCE_API_KEY", "")
        self._api_secret = os.getenv("BINANCE_API_SECRET", "")

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def _sign(self, params: dict) -> dict:
        signed = {**params, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
        query = urlencode(signed)
        signature = hmac.new(self._api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        signed["signature"] = signature
        return signed

    async def _signed_get(self, base: str, path: str, params: dict | None = None) -> Any:
        self._require_configured()
        signed = self._sign(params or {})
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{base}{path}", params=signed, headers={"X-MBX-APIKEY": self._api_key})
            r.raise_for_status()
            return r.json()

    async def _public_get(self, base: str, path: str, params: dict | None = None) -> Any:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{base}{path}", params=params or {})
            r.raise_for_status()
            return r.json()

    async def check_permissions(self) -> PermissionCheck:
        if not self.configured:
            return PermissionCheck(detectable=False, note="No API credentials configured")
        try:
            data = await self._signed_get(SPOT_BASE, "/sapi/v1/account/apiRestrictions")
        except Exception as e:
            return PermissionCheck(detectable=False, note=f"Permission check failed: {e!r}")

        return PermissionCheck(
            detectable=True,
            withdraw_enabled=bool(data.get("enableWithdrawals")),
            trade_enabled=bool(data.get("enableSpotAndMarginTrading") or data.get("enableFutures")),
            read_enabled=bool(data.get("enableReading", True)),
            raw=data,
        )

    async def get_account_info(self) -> dict:
        return await self._signed_get(FUTURES_BASE, "/fapi/v2/account")

    async def get_balances(self) -> list[dict]:
        data = await self._signed_get(FUTURES_BASE, "/fapi/v2/balance")
        return [
            {
                "asset": b["asset"],
                "balance": float(b["balance"]),
                "available": float(b["availableBalance"]),
            }
            for b in data
        ]

    async def get_positions(self) -> list[dict]:
        data = await self._signed_get(FUTURES_BASE, "/fapi/v2/positionRisk")
        return [
            {
                "symbol": p["symbol"],
                "side": "LONG" if float(p["positionAmt"]) > 0 else "SHORT",
                "size": float(p["positionAmt"]),
                "entry_price": float(p["entryPrice"]),
                "mark_price": float(p["markPrice"]),
                "unrealized_pnl": float(p["unRealizedProfit"]),
                "leverage": float(p["leverage"]),
            }
            for p in data
            if float(p["positionAmt"]) != 0
        ]

    async def get_open_orders(self) -> list[dict]:
        data = await self._signed_get(FUTURES_BASE, "/fapi/v1/openOrders")
        return [
            {
                "order_id": o["orderId"],
                "symbol": o["symbol"],
                "side": o["side"],
                "type": o["type"],
                "price": float(o["price"]),
                "qty": float(o["origQty"]),
                "status": o["status"],
            }
            for o in data
        ]

    async def get_funding_rate(self, symbol: str) -> dict:
        data = await self._public_get(FUTURES_BASE, "/fapi/v1/premiumIndex", {"symbol": symbol.upper()})
        return {
            "symbol": data["symbol"],
            "funding_rate": float(data["lastFundingRate"]),
            "next_funding_time": data["nextFundingTime"],
            "mark_price": float(data["markPrice"]),
        }

    async def get_open_interest(self, symbol: str) -> dict:
        data = await self._public_get(FUTURES_BASE, "/fapi/v1/openInterest", {"symbol": symbol.upper()})
        return {"symbol": data["symbol"], "open_interest": float(data["openInterest"])}

    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        data = await self._public_get(FUTURES_BASE, "/fapi/v1/depth", {"symbol": symbol.upper(), "limit": limit})
        return {
            "bids": [[float(p), float(q)] for p, q in data["bids"]],
            "asks": [[float(p), float(q)] for p, q in data["asks"]],
        }

    async def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        data = await self._public_get(FUTURES_BASE, "/fapi/v1/trades", {"symbol": symbol.upper(), "limit": limit})
        return [
            {"price": float(t["price"]), "qty": float(t["qty"]), "time": t["time"], "is_buyer_maker": t["isBuyerMaker"]}
            for t in data
        ]

    async def get_candles(self, symbol: str, interval: str = "5m", limit: int = 100) -> list[dict]:
        data = await self._public_get(
            FUTURES_BASE, "/fapi/v1/klines", {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        )
        return [
            {
                "time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            for k in data
        ]

    async def get_exchange_status(self) -> dict:
        try:
            await self._public_get(FUTURES_BASE, "/fapi/v1/ping")
            return {"exchange": self.name, "status": "operational", "reachable": True}
        except Exception as e:
            return {"exchange": self.name, "status": "unreachable", "reachable": False, "error": repr(e)}
