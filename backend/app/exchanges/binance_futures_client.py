"""Binance USDT-M Futures trading client (Phase 22).

This is the ONLY module in the codebase that can reach a Binance order-entry
endpoint, and it is deliberately separate from the read-only adapter stack
in app/exchanges/binance.py (which remains structurally unable to trade).

Safety properties:
  - Credentials come from env-backed settings only; they are never accepted
    from a request, never persisted, and never included in anything this
    module returns or raises.
  - A client pointed at production (`testnet=False`) refuses to construct
    unless BINANCE_LIVE_ENABLED=true - the server-side master lock.
  - Every order carries a caller-supplied or generated newClientOrderId so
    an accidental double-submit is rejected by the exchange itself.
  - Timestamp drift is handled by syncing a server-time offset and retrying
    once; rate limits surface as BinanceRateLimitError so callers back off
    instead of hammering.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.exchanges.binance_errors import (
    BinanceError,
    BinanceNetworkError,
    BinanceNotConfigured,
    BinanceTimestampError,
    map_binance_error,
)
from app.exchanges.binance_models import (
    BinanceAccountSummary,
    BinanceBalance,
    BinanceOrder,
    BinancePosition,
    BinanceUserTrade,
)

PROD_BASE = "https://fapi.binance.com"
TESTNET_BASE = "https://testnet.binancefuture.com"

RECV_WINDOW_MS = 5000


class LiveTradingLocked(RuntimeError):
    """Raised when an order-capable production (non-testnet) client is
    constructed - or a write is attempted - while it isn't permitted."""


class BinanceFuturesClient:
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        testnet: bool = True,
        timeout: float = 15.0,
        read_only: bool = False,
    ):
        """`read_only=True` (Phase 23) permits a PRODUCTION client purely for
        signed account reads - balances, positions, orders, trades, income -
        so the Binance Real portfolio is viewable while live trading stays
        locked. Every write path (_post/_delete) hard-refuses on a read-only
        client, so this cannot become an order side door."""
        if not testnet and not read_only and not settings.binance_live_enabled:
            raise LiveTradingLocked(
                "BINANCE_LIVE_ENABLED is false - refusing to construct a production trading client"
            )
        self._api_key = api_key if api_key is not None else settings.binance_api_key
        self._api_secret = api_secret if api_secret is not None else settings.binance_api_secret
        self.testnet = testnet
        self.read_only = read_only
        self.base_url = TESTNET_BASE if testnet else PROD_BASE
        self.timeout = timeout
        # server_time - local_time, learned lazily and re-synced on -1021
        self._time_offset_ms = 0

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._api_secret)

    # ------------------------------------------------------------- plumbing

    def _timestamp(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    def _sign(self, params: dict) -> dict:
        signed = {k: v for k, v in params.items() if v is not None}
        signed["timestamp"] = self._timestamp()
        signed["recvWindow"] = RECV_WINDOW_MS
        query = urlencode(signed)
        signed["signature"] = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return signed

    async def _sync_time(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(f"{self.base_url}/fapi/v1/time")
                r.raise_for_status()
                server_ms = int(r.json()["serverTime"])
                self._time_offset_ms = server_ms - int(time.time() * 1000)
        except Exception as e:  # keep the original -1021 as the real story
            raise BinanceNetworkError(f"failed to sync Binance server time: {e!r}") from e

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        signed: bool = False,
        _retried: bool = False,
    ):
        if signed and not self.configured:
            raise BinanceNotConfigured(
                f"Binance {'testnet' if self.testnet else 'live'} API credentials are not configured"
            )
        params = params or {}
        query = self._sign(params) if signed else params
        headers = {"X-MBX-APIKEY": self._api_key} if signed else {}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.request(method, f"{self.base_url}{path}", params=query, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise BinanceNetworkError(f"Binance request failed: {type(e).__name__}") from e

        if r.status_code // 100 == 2:
            return r.json()

        try:
            payload = r.json()
        except ValueError:
            payload = None
        error = map_binance_error(r.status_code, payload)

        # One transparent retry after re-syncing the clock: timestamp drift
        # is an environment problem, not a caller decision.
        if isinstance(error, BinanceTimestampError) and signed and not _retried:
            await self._sync_time()
            return await self._request(method, path, params, signed=True, _retried=True)

        raise error

    async def _get(self, path: str, params: dict | None = None, signed: bool = False):
        return await self._request("GET", path, params, signed)

    async def _post(self, path: str, params: dict | None = None):
        if self.read_only:
            raise LiveTradingLocked("read-only Binance client - write operations are structurally disabled")
        return await self._request("POST", path, params, signed=True)

    async def _delete(self, path: str, params: dict | None = None):
        if self.read_only:
            raise LiveTradingLocked("read-only Binance client - write operations are structurally disabled")
        return await self._request("DELETE", path, params, signed=True)

    # ------------------------------------------------------------ read side

    async def ping(self) -> bool:
        await self._get("/fapi/v1/ping")
        return True

    async def get_account_info(self) -> BinanceAccountSummary:
        return BinanceAccountSummary.from_api(await self._get("/fapi/v2/account", signed=True))

    async def get_balances(self) -> list[BinanceBalance]:
        data = await self._get("/fapi/v2/balance", signed=True)
        return [BinanceBalance.from_api(b) for b in data]

    async def get_positions(self, symbol: str | None = None) -> list[BinancePosition]:
        params = {"symbol": symbol.upper()} if symbol else {}
        data = await self._get("/fapi/v2/positionRisk", params, signed=True)
        return [BinancePosition.from_api(p) for p in data if float(p.get("positionAmt", 0)) != 0]

    async def get_open_orders(self, symbol: str | None = None) -> list[BinanceOrder]:
        params = {"symbol": symbol.upper()} if symbol else {}
        data = await self._get("/fapi/v1/openOrders", params, signed=True)
        return [BinanceOrder.from_api(o) for o in data]

    async def get_order_history(self, symbol: str, limit: int = 50) -> list[BinanceOrder]:
        data = await self._get("/fapi/v1/allOrders", {"symbol": symbol.upper(), "limit": limit}, signed=True)
        return [BinanceOrder.from_api(o) for o in data]

    async def get_trade_history(self, symbol: str, limit: int = 50) -> list[BinanceUserTrade]:
        data = await self._get("/fapi/v1/userTrades", {"symbol": symbol.upper(), "limit": limit}, signed=True)
        return [BinanceUserTrade.from_api(t) for t in data]

    async def get_mark_price(self, symbol: str) -> float:
        data = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol.upper()})
        return float(data["markPrice"])

    async def get_income_history(self, limit: int = 50, income_type: str | None = None) -> list[dict]:
        """Recent account income rows (realized PnL, commission, funding
        fees...), normalized and safe to return to the UI."""
        params: dict = {"limit": min(max(limit, 1), 1000)}
        if income_type:
            params["incomeType"] = income_type
        data = await self._get("/fapi/v1/income", params, signed=True)
        return [
            {
                "symbol": row.get("symbol") or None,
                "income_type": row.get("incomeType"),
                "income": float(row.get("income", 0)),
                "asset": row.get("asset"),
                "info": row.get("info"),
                "time": row.get("time"),
            }
            for row in data
        ]

    async def get_daily_realized_pnl(self) -> float:
        """Sum of REALIZED_PNL income since UTC midnight - the number the
        real-trading daily-loss gate compares against."""
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        data = await self._get(
            "/fapi/v1/income",
            {"incomeType": "REALIZED_PNL", "startTime": int(midnight.timestamp() * 1000), "limit": 1000},
            signed=True,
        )
        return sum(float(row.get("income", 0)) for row in data)

    # ----------------------------------------------------------- write side

    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        return await self._post(
            "/fapi/v1/leverage", {"symbol": symbol.upper(), "leverage": int(leverage)}
        )

    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        try:
            return await self._post(
                "/fapi/v1/marginType", {"symbol": symbol.upper(), "marginType": margin_type.upper()}
            )
        except BinanceError as e:
            # -4046: "No need to change margin type" - already what we asked for
            if e.code == -4046:
                return {"msg": "already set"}
            raise

    @staticmethod
    def _client_order_id(prefix: str = "qx") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:20]}"

    async def place_market_order(
        self,
        symbol: str,
        side: str,  # BUY | SELL
        quantity: float,
        reduce_only: bool = False,
        client_order_id: str | None = None,
    ) -> BinanceOrder:
        params = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": "MARKET",
            "quantity": quantity,
            "newClientOrderId": client_order_id or self._client_order_id(),
            "newOrderRespType": "RESULT",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        return BinanceOrder.from_api(await self._post("/fapi/v1/order", params))

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        reduce_only: bool = False,
        time_in_force: str = "GTC",
        client_order_id: str | None = None,
    ) -> BinanceOrder:
        params = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": "LIMIT",
            "quantity": quantity,
            "price": price,
            "timeInForce": time_in_force,
            "newClientOrderId": client_order_id or self._client_order_id(),
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        return BinanceOrder.from_api(await self._post("/fapi/v1/order", params))

    async def place_stop_loss(
        self,
        symbol: str,
        position_side: str,  # LONG | SHORT (the position being protected)
        stop_price: float,
        quantity: float | None = None,
        client_order_id: str | None = None,
    ) -> BinanceOrder:
        """Reduce-only STOP_MARKET protecting an open position. With no
        quantity it uses closePosition=true (close whatever remains)."""
        params = {
            "symbol": symbol.upper(),
            "side": "SELL" if position_side.upper() == "LONG" else "BUY",
            "type": "STOP_MARKET",
            "stopPrice": stop_price,
            "newClientOrderId": client_order_id or self._client_order_id("qxsl"),
            "workingType": "MARK_PRICE",
        }
        if quantity is not None:
            params["quantity"] = quantity
            params["reduceOnly"] = "true"
        else:
            params["closePosition"] = "true"
        return BinanceOrder.from_api(await self._post("/fapi/v1/order", params))

    async def place_take_profit(
        self,
        symbol: str,
        position_side: str,
        stop_price: float,
        quantity: float | None = None,
        client_order_id: str | None = None,
    ) -> BinanceOrder:
        """Reduce-only TAKE_PROFIT_MARKET; closePosition when no quantity."""
        params = {
            "symbol": symbol.upper(),
            "side": "SELL" if position_side.upper() == "LONG" else "BUY",
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": stop_price,
            "newClientOrderId": client_order_id or self._client_order_id("qxtp"),
            "workingType": "MARK_PRICE",
        }
        if quantity is not None:
            params["quantity"] = quantity
            params["reduceOnly"] = "true"
        else:
            params["closePosition"] = "true"
        return BinanceOrder.from_api(await self._post("/fapi/v1/order", params))

    async def cancel_order(self, symbol: str, order_id: int) -> BinanceOrder:
        return BinanceOrder.from_api(
            await self._delete("/fapi/v1/order", {"symbol": symbol.upper(), "orderId": int(order_id)})
        )

    async def cancel_all_orders(self, symbol: str) -> dict:
        return await self._delete("/fapi/v1/allOpenOrders", {"symbol": symbol.upper()})
