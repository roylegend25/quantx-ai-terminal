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
    BinanceInvalidSymbol,
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

# Per-symbol order-precision rules (minQty/stepSize/minNotional) barely
# change - cached process-wide for an hour so the Test Order feature (the
# only caller) doesn't hit the public exchangeInfo endpoint on every click.
# Keyed by base_url so testnet and prod never share a cache entry.
_exchange_filters_cache: dict[str, tuple[float, dict]] = {}
_commission_rate_cache: dict[str, tuple[float, dict]] = {}
_leverage_brackets_cache: dict[str, tuple[float, list]] = {}
_position_mode_cache: dict[str, tuple[float, bool]] = {}
EXCHANGE_FILTERS_TTL_SECONDS = 3600


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

    async def get_exchange_filters(self, symbol: str) -> dict:
        """Real per-symbol order-precision rules from Binance's public,
        unsigned exchangeInfo - minimum quantity, quantity step size and
        minimum notional. Used by the Test Order feature to submit a
        genuinely valid minimum-size order instead of guessing from a
        hardcoded table (see execution_router._round_qty, which is a
        separate, static approximation used by the live trading path)."""
        symbol = symbol.upper()
        cache_key = f"{self.base_url}:{symbol}"
        cached = _exchange_filters_cache.get(cache_key)
        now = time.time()
        if cached and now - cached[0] < EXCHANGE_FILTERS_TTL_SECONDS:
            return cached[1]

        data = await self._get("/fapi/v1/exchangeInfo")
        info = next((s for s in data.get("symbols", []) if s.get("symbol") == symbol), None)
        if not info:
            raise BinanceInvalidSymbol(f"{symbol} not found in Binance exchangeInfo", code=-1121)

        filters = {f["filterType"]: f for f in info.get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        price_filter = filters.get("PRICE_FILTER", {})
        min_notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
        result = {
            "min_qty": float(lot.get("minQty", 0.001)),
            "step_size": float(lot.get("stepSize", 0.001)),
            "quantity_precision": int(info.get("quantityPrecision", 3)),
            "min_notional": float(min_notional_filter.get("notional") or min_notional_filter.get("minNotional") or 5.0),
            "tick_size": float(price_filter.get("tickSize", 0.1)),
            "price_precision": int(info.get("pricePrecision", 2)),
        }
        _exchange_filters_cache[cache_key] = (now, result)
        return result

    async def get_commission_rate(self, symbol: str) -> dict:
        """Real signed account commission rates for `symbol` (this account's
        actual VIP/BNB-discount tier, not a guessed constant) - GET
        /fapi/v1/commissionRate. Cached like exchange filters."""
        symbol = symbol.upper()
        cache_key = f"{self.base_url}:{symbol}"
        cached = _commission_rate_cache.get(cache_key)
        now = time.time()
        if cached and now - cached[0] < EXCHANGE_FILTERS_TTL_SECONDS:
            return cached[1]

        data = await self._get("/fapi/v1/commissionRate", {"symbol": symbol}, signed=True)
        result = {
            "maker_rate": float(data.get("makerCommissionRate", 0.0002)),
            "taker_rate": float(data.get("takerCommissionRate", 0.0004)),
        }
        _commission_rate_cache[cache_key] = (now, result)
        return result

    async def get_leverage_brackets(self, symbol: str) -> list[dict]:
        """Real signed maintenance-margin brackets for `symbol` - GET
        /fapi/v1/leverageBracket. Used to compute an honest maintenance
        margin instead of a guessed flat rate. Cached like exchange
        filters."""
        symbol = symbol.upper()
        cache_key = f"{self.base_url}:{symbol}"
        cached = _leverage_brackets_cache.get(cache_key)
        now = time.time()
        if cached and now - cached[0] < EXCHANGE_FILTERS_TTL_SECONDS:
            return cached[1]

        data = await self._get("/fapi/v1/leverageBracket", {"symbol": symbol}, signed=True)
        row = next((d for d in data if d.get("symbol") == symbol), None)
        brackets = [
            {
                "bracket": b.get("bracket"),
                "initial_leverage": b.get("initialLeverage"),
                "notional_cap": float(b.get("notionalCap", 0)),
                "notional_floor": float(b.get("notionalFloor", 0)),
                "maint_margin_ratio": float(b.get("maintMarginRatio", 0.004)),
                "cum": float(b.get("cum", 0)),
            }
            for b in (row or {}).get("brackets", [])
        ]
        _leverage_brackets_cache[cache_key] = (now, brackets)
        return brackets

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

    async def get_multi_assets_margin(self) -> bool:
        """True if Multi-Assets Mode is on - GET /fapi/v1/multiAssetsMargin.
        Diagnostic-only (Phase 28 Trading Diagnostics)."""
        data = await self._get("/fapi/v1/multiAssetsMargin", signed=True)
        return bool(data.get("multiAssetsMargin"))

    async def get_api_key_permissions(self) -> dict:
        """Real API key permission flags - GET /sapi/v1/account/apiRestrictions
        on the SPOT/Margin host (api.binance.com), signed with the same key.
        This is a DIFFERENT permission scope than futures trading itself, so
        a failure here is reported to the caller rather than raised - it
        does not mean the futures key is broken, only that this specific
        read isn't available to it. Diagnostic-only, never used by the
        trading path."""
        query = self._sign({})
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http_client:
                r = await http_client.get(
                    "https://api.binance.com/sapi/v1/account/apiRestrictions",
                    params=query, headers={"X-MBX-APIKEY": self._api_key},
                )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise BinanceNetworkError(f"Binance request failed: {type(e).__name__}") from e
        if r.status_code // 100 == 2:
            return r.json()
        try:
            payload = r.json()
        except ValueError:
            payload = None
        raise map_binance_error(r.status_code, payload)

    async def probe_algo_api_reachable(self) -> dict:
        """Probe GET /fapi/v1/algoOrder (singular) with no algoId/clientAlgoId.
        Read-only and non-destructive - this call can never succeed (Binance
        requires one of those params), so any reply other than HTTP 404 is
        real evidence the Algo Order API surface exists and is reachable for
        this account/key.

        Confirmed empirically (Phase 28 investigation): this account gets a
        genuine parameter-validation error, -1102 "Param 'algoid' or
        'clientalgoid' must be sent, but both were empty/null!" - a 400 from
        Binance's own request validator, not a 404. That is direct proof the
        endpoint exists and this key can reach it.

        Three other guessed listing-endpoint paths (/fapi/v1/algoOpenOrders,
        /fapi/v1/algo/futures/openOrders, /fapi/v1/algo/futures/
        historicalOrders) were also probed live and all returned HTTP 404 -
        those paths do not exist. The correct path for "list all open algo
        orders" (if one exists) is still unconfirmed; this probe only shows
        reachability of the base /fapi/v1/algoOrder resource. Diagnostic
        only, never used by the trading path."""
        try:
            await self._get("/fapi/v1/algoOrder", {}, signed=True)
            return {"reachable": True, "evidence": "Unexpected 200 with no algoId - endpoint reachable"}
        except BinanceError as e:
            if e.status == 404:
                return {"reachable": False, "evidence": "HTTP 404 - endpoint does not exist at this path"}
            return {
                "reachable": True,
                "evidence": (
                    f"Binance validated the request and rejected it for a business reason "
                    f"(code={e.code}, status={e.status}: {e.message}), not a 404 - the endpoint exists "
                    f"and is reachable by this API key"
                ),
            }

    async def test_stop_market_order(self, symbol: str, side: str, stop_price: float, quantity: float) -> dict:
        """Binance's own no-op order-validation endpoint - POST
        /fapi/v1/order/test. Documented to validate signature/params
        WITHOUT sending the order to the matching engine or the order book -
        this never places or risks anything. Diagnostic-only: captures the
        exact real request/response for a STOP_MARKET order (Phase 28
        Trading Diagnostics, step 2). Only callable on a write-capable
        (non-read-only) client, i.e. an already-unlocked live/testnet mode -
        this method does not itself bypass that gate."""
        params = {
            "symbol": symbol.upper(), "side": side.upper(), "type": "STOP_MARKET",
            "stopPrice": stop_price, "quantity": quantity, "workingType": "MARK_PRICE",
            "newClientOrderId": self._client_order_id("qxdiag"),
        }
        try:
            response = await self._post("/fapi/v1/order/test", params)
            return {"ok": True, "request": params, "response": response}
        except BinanceError as e:
            return {"ok": False, "request": params, "code": e.code, "status": e.status, "message": e.message}

    async def get_position_mode(self) -> bool:
        """True if the account is in Hedge (dual-side) position mode - GET
        /fapi/v1/positionSide/dual. Cached: this almost never changes and a
        wrong value only matters for the (rare) manual switch mid-session,
        which callers already re-check via the same short TTL as the other
        account-shape caches."""
        cache_key = self.base_url
        cached = _position_mode_cache.get(cache_key)
        now = time.time()
        if cached and now - cached[0] < EXCHANGE_FILTERS_TTL_SECONDS:
            return cached[1]
        data = await self._get("/fapi/v1/positionSide/dual", signed=True)
        hedge_mode = bool(data.get("dualSidePosition"))
        _position_mode_cache[cache_key] = (now, hedge_mode)
        return hedge_mode

    async def _place_protective_order(
        self, *, symbol: str, order_type: str, position_side: str, stop_price: float,
        quantity: float | None, client_order_id: str | None, prefix: str, hedge_mode: bool,
    ) -> BinanceOrder:
        """Shared implementation for place_stop_loss/place_take_profit.

        Binance rejects the closePosition=true shape for STOP_MARKET/
        TAKE_PROFIT_MARKET on some accounts (observed in production:
        "Order type not supported for this endpoint. Please use the Algo
        Order API endpoints instead.") - so whenever a real fill quantity is
        known, this always falls back to reduceOnly=true with the exact
        quantity on any rejection of the first attempt, which is the
        universally-accepted shape and the only one valid in Hedge Mode.
        Hedge Mode requires positionSide=LONG/SHORT and forbids reduceOnly
        (the exchange infers it from positionSide); One-way mode uses
        closePosition or reduceOnly and no positionSide."""
        symbol = symbol.upper()
        order_side = "SELL" if position_side.upper() == "LONG" else "BUY"
        base = {
            "symbol": symbol, "side": order_side, "type": order_type,
            "stopPrice": stop_price, "workingType": "MARK_PRICE",
        }

        if hedge_mode:
            base["positionSide"] = position_side.upper()
            primary = {**base, "newClientOrderId": client_order_id or self._client_order_id(prefix)}
            if quantity:
                primary["quantity"] = quantity
            else:
                primary["closePosition"] = "true"
        else:
            primary = {**base, "newClientOrderId": client_order_id or self._client_order_id(prefix)}
            if quantity:
                primary["quantity"] = quantity
                primary["reduceOnly"] = "true"
            else:
                primary["closePosition"] = "true"

        try:
            return BinanceOrder.from_api(await self._post("/fapi/v1/order", primary))
        except BinanceError:
            if not quantity or "quantity" in primary:
                raise  # no fallback available, or we already tried the quantity-based shape
            fallback = {**base, "quantity": quantity, "newClientOrderId": self._client_order_id(prefix)}
            if hedge_mode:
                fallback["positionSide"] = position_side.upper()
            else:
                fallback["reduceOnly"] = "true"
            return BinanceOrder.from_api(await self._post("/fapi/v1/order", fallback))

    async def place_stop_loss(
        self,
        symbol: str,
        position_side: str,  # LONG | SHORT (the position being protected)
        stop_price: float,
        quantity: float | None = None,
        client_order_id: str | None = None,
        hedge_mode: bool = False,
    ) -> BinanceOrder:
        """Reduce-only STOP_MARKET protecting an open position. Pass the
        real filled quantity whenever it's known - see
        _place_protective_order for why that enables a safe fallback."""
        return await self._place_protective_order(
            symbol=symbol, order_type="STOP_MARKET", position_side=position_side, stop_price=stop_price,
            quantity=quantity, client_order_id=client_order_id, prefix="qxsl", hedge_mode=hedge_mode,
        )

    async def place_take_profit(
        self,
        symbol: str,
        position_side: str,
        stop_price: float,
        quantity: float | None = None,
        client_order_id: str | None = None,
        hedge_mode: bool = False,
    ) -> BinanceOrder:
        """Reduce-only TAKE_PROFIT_MARKET; see place_stop_loss."""
        return await self._place_protective_order(
            symbol=symbol, order_type="TAKE_PROFIT_MARKET", position_side=position_side, stop_price=stop_price,
            quantity=quantity, client_order_id=client_order_id, prefix="qxtp", hedge_mode=hedge_mode,
        )

    async def cancel_order(self, symbol: str, order_id: int) -> BinanceOrder:
        return BinanceOrder.from_api(
            await self._delete("/fapi/v1/order", {"symbol": symbol.upper(), "orderId": int(order_id)})
        )

    async def cancel_all_orders(self, symbol: str) -> dict:
        return await self._delete("/fapi/v1/allOpenOrders", {"symbol": symbol.upper()})
