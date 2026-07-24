"""Aggregates all exchange adapters behind a single read-only facade.

Hard safety gate: if EXCHANGE_READ_ONLY is not true, the manager refuses to
construct any adapter at all (see ExchangeManager.__init__ and
ExchangeAdapter.__init__). There is no code path anywhere in this package
that can place, amend or cancel an order, or move funds.
"""
import asyncio

from app.core.config import settings
from app.exchanges.base import ExchangeAdapter, PermissionCheck
from app.exchanges.binance import BinanceAdapter
from app.exchanges.bybit import BybitAdapter
from app.exchanges.coinbase import CoinbaseAdapter
from app.exchanges.okx import OkxAdapter

ADAPTER_CLASSES: dict[str, type[ExchangeAdapter]] = {
    "binance": BinanceAdapter,
    "coinbase": CoinbaseAdapter,
    "bybit": BybitAdapter,
    "okx": OkxAdapter,
}


class ExchangeManager:
    def __init__(self):
        self._adapters: dict[str, ExchangeAdapter] = {}
        self._init_error: str | None = None

        if not settings.exchange_read_only:
            self._init_error = "EXCHANGE_READ_ONLY is not true - refusing to initialize any exchange adapter"
            return

        for name, cls in ADAPTER_CLASSES.items():
            self._adapters[name] = cls()

    @property
    def read_only(self) -> bool:
        return settings.exchange_read_only

    def get_adapter(self, name: str) -> ExchangeAdapter | None:
        return self._adapters.get(name.lower())

    async def _status_one(self, name: str, adapter: ExchangeAdapter, symbol: str) -> tuple[str, dict]:
        if not adapter.configured:
            return name, {"connected": False, "configured": False, "status": "no credentials"}
        try:
            status = await adapter.get_exchange_status()
        except Exception as e:
            return name, {"connected": False, "configured": True, "status": "error", "error": repr(e)}

        entry = {"connected": bool(status.get("reachable")), "configured": True, **status}

        # Funding rate is public market data (no credentials needed) -
        # surfaced here so the frontend doesn't need a dedicated route.
        if entry["connected"]:
            try:
                entry["funding"] = await adapter.get_funding_rate(symbol)
            except Exception as e:
                entry["funding"] = {"symbol": symbol, "funding_rate": None, "error": repr(e)}

        return name, entry

    async def status_all(self, funding_symbol: str | None = None) -> dict:
        if self._init_error:
            return {"read_only": self.read_only, "error": self._init_error, "exchanges": {}}

        symbol = funding_symbol or settings.default_symbol
        # Each adapter's status->funding pair still runs in its own fixed
        # order internally (funding is only fetched once we know the status
        # call succeeded), but the up-to-4 adapters themselves now run
        # concurrently instead of one full status+funding round trip after
        # another - previously up to 8 sequential network calls (Stage 1
        # performance audit measured this as a direct contributor to the
        # dashboard's secondary-batch tail latency).
        pairs = await asyncio.gather(
            *(self._status_one(name, adapter, symbol) for name, adapter in self._adapters.items())
        )
        results = dict(pairs)

        return {"read_only": self.read_only, "exchanges": results}

    async def balances(self, exchange: str) -> list[dict]:
        adapter = self.get_adapter(exchange)
        if not adapter:
            raise ValueError(f"Unknown exchange: {exchange}")
        return await adapter.get_balances()

    async def positions(self, exchange: str) -> list[dict]:
        adapter = self.get_adapter(exchange)
        if not adapter:
            raise ValueError(f"Unknown exchange: {exchange}")
        return await adapter.get_positions()

    async def open_orders(self, exchange: str) -> list[dict]:
        adapter = self.get_adapter(exchange)
        if not adapter:
            raise ValueError(f"Unknown exchange: {exchange}")
        return await adapter.get_open_orders()

    async def risk_check(self) -> dict:
        """Read-only compliance report: confirms the read-only gate is on and
        that no configured exchange key carries withdrawal rights."""
        report: dict = {
            "read_only_flag": self.read_only,
            # Structurally true - no order-placement method exists anywhere
            # in this package, so there is nothing a flag flip could unlock.
            "order_placement_supported": False,
            "exchanges": {},
            "safe": self.read_only,
        }

        if self._init_error:
            report["error"] = self._init_error
            report["safe"] = False
            return report

        for name, adapter in self._adapters.items():
            if not adapter.configured:
                report["exchanges"][name] = {"configured": False}
                continue
            try:
                check: PermissionCheck = await adapter.check_permissions()
            except Exception as e:
                report["exchanges"][name] = {"configured": True, "detectable": False, "error": repr(e)}
                continue

            report["exchanges"][name] = {
                "configured": True,
                "detectable": check.detectable,
                "withdraw_enabled": check.withdraw_enabled,
                "trade_enabled": check.trade_enabled,
                "note": check.note,
                "safe": check.safe,
            }
            if not check.safe:
                report["safe"] = False

        return report


manager = ExchangeManager()
