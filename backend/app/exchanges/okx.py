"""OKX V5 read-only adapter.

Only ever issues GET requests against documented read endpoints. No order,
transfer or withdrawal endpoint is ever called.
"""
import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from app.exchanges.base import ExchangeAdapter, PermissionCheck

BASE = "https://www.okx.com"


class OkxAdapter(ExchangeAdapter):
    name = "okx"

    def __init__(self):
        super().__init__()
        self._api_key = os.getenv("OKX_API_KEY", "")
        self._api_secret = os.getenv("OKX_API_SECRET", "")
        self._passphrase = os.getenv("OKX_API_PASSPHRASE", "")

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._api_secret and self._passphrase)

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _sign(self, timestamp: str, method: str, path: str) -> str:
        message = f"{timestamp}{method}{path}"
        digest = hmac.new(self._api_secret.encode(), message.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    async def _signed_get(self, path: str, params: dict | None = None) -> Any:
        self._require_configured()
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        timestamp = self._timestamp()
        signature = self._sign(timestamp, "GET", path + query)
        headers = {
            "OK-ACCESS-KEY": self._api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self._passphrase,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{BASE}{path}{query}", headers=headers)
            r.raise_for_status()
            body = r.json()
            if body.get("code") not in ("0", 0):
                raise RuntimeError(f"OKX error {body.get('code')}: {body.get('msg')}")
            return body["data"]

    async def _public_get(self, path: str, params: dict | None = None) -> Any:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{BASE}{path}", params=params or {})
            r.raise_for_status()
            body = r.json()
            if body.get("code") not in ("0", 0):
                raise RuntimeError(f"OKX error {body.get('code')}: {body.get('msg')}")
            return body["data"]

    async def check_permissions(self) -> PermissionCheck:
        if not self.configured:
            return PermissionCheck(detectable=False, note="No API credentials configured")
        try:
            data = await self._signed_get("/api/v5/account/config")
        except Exception as e:
            return PermissionCheck(detectable=False, note=f"Permission check failed: {e!r}")

        row = data[0] if data else {}
        perms = [p.strip() for p in (row.get("perm") or "").lower().split(",") if p.strip()]
        return PermissionCheck(
            detectable=bool(perms),
            withdraw_enabled=("withdraw" in perms) if perms else None,
            trade_enabled=("trade" in perms) if perms else None,
            read_enabled=("read_only" in perms) if perms else None,
            raw=row,
        )

    async def get_account_info(self) -> dict:
        data = await self._signed_get("/api/v5/account/balance")
        return {"accounts": data}

    async def get_balances(self) -> list[dict]:
        data = await self._signed_get("/api/v5/account/balance")
        balances = []
        for acct in data:
            for d in acct.get("details", []):
                bal = float(d.get("cashBal") or 0)
                if bal != 0:
                    balances.append(
                        {"asset": d["ccy"], "balance": bal, "available": float(d.get("availBal") or 0)}
                    )
        return balances

    async def get_positions(self) -> list[dict]:
        data = await self._signed_get("/api/v5/account/positions")
        return [
            {
                "symbol": p["instId"],
                "side": p.get("posSide", "net"),
                "size": float(p["pos"] or 0),
                "entry_price": float(p.get("avgPx") or 0),
                "mark_price": float(p.get("markPx") or 0),
                "unrealized_pnl": float(p.get("upl") or 0),
                "leverage": float(p.get("lever") or 0),
            }
            for p in data
            if float(p["pos"] or 0) != 0
        ]

    async def get_open_orders(self) -> list[dict]:
        data = await self._signed_get("/api/v5/trade/orders-pending")
        return [
            {
                "order_id": o["ordId"],
                "symbol": o["instId"],
                "side": o["side"],
                "type": o["ordType"],
                "price": float(o.get("px") or 0),
                "qty": float(o.get("sz") or 0),
                "status": o["state"],
            }
            for o in data
        ]

    async def get_funding_rate(self, symbol: str) -> dict:
        data = await self._public_get("/api/v5/public/funding-rate", {"instId": symbol.upper()})
        row = data[0] if data else {}
        return {
            "symbol": row.get("instId", symbol.upper()),
            "funding_rate": float(row.get("fundingRate") or 0),
            "next_funding_time": row.get("nextFundingTime"),
        }

    async def get_open_interest(self, symbol: str) -> dict:
        data = await self._public_get("/api/v5/public/open-interest", {"instId": symbol.upper()})
        row = data[0] if data else {}
        return {"symbol": row.get("instId", symbol.upper()), "open_interest": float(row.get("oi") or 0)}

    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        data = await self._public_get("/api/v5/market/books", {"instId": symbol.upper(), "sz": limit})
        row = data[0] if data else {"bids": [], "asks": []}
        return {
            "bids": [[float(p), float(q)] for p, q, *_ in row.get("bids", [])],
            "asks": [[float(p), float(q)] for p, q, *_ in row.get("asks", [])],
        }

    async def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        data = await self._public_get("/api/v5/market/trades", {"instId": symbol.upper(), "limit": limit})
        return [
            {"price": float(t["px"]), "qty": float(t["sz"]), "time": int(t["ts"]), "side": t.get("side")}
            for t in data
        ]

    async def get_candles(self, symbol: str, interval: str = "5m", limit: int = 100) -> list[dict]:
        bar_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
        data = await self._public_get(
            "/api/v5/market/candles",
            {"instId": symbol.upper(), "bar": bar_map.get(interval, "5m"), "limit": limit},
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
            for k in reversed(data)
        ]

    async def get_exchange_status(self) -> dict:
        try:
            data = await self._public_get("/api/v5/system/status")
            ongoing = [d for d in data if d.get("state") in ("scheduled", "ongoing")]
            return {
                "exchange": self.name,
                "status": "maintenance" if ongoing else "operational",
                "reachable": True,
                "incidents": ongoing,
            }
        except Exception as e:
            return {"exchange": self.name, "status": "unreachable", "reachable": False, "error": repr(e)}
