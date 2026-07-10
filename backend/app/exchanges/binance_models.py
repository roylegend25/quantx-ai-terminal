"""Typed views of Binance Futures API payloads.

Plain dataclasses with `from_api()` constructors so the trading client and
execution router never pass raw exchange dicts around. `to_dict()` output is
what API endpoints return - it never includes credentials or signatures
(none are ever stored on these objects to begin with).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class BinanceBalance:
    asset: str
    balance: float
    available: float
    cross_unrealized_pnl: float = 0.0

    @property
    def locked(self) -> float:
        return max(0.0, self.balance - self.available)

    @classmethod
    def from_api(cls, raw: dict) -> "BinanceBalance":
        return cls(
            asset=raw.get("asset", ""),
            balance=_f(raw.get("balance")),
            available=_f(raw.get("availableBalance")),
            cross_unrealized_pnl=_f(raw.get("crossUnPnl")),
        )

    def to_dict(self) -> dict:
        return {**asdict(self), "locked": round(self.locked, 8)}


@dataclass
class BinancePosition:
    symbol: str
    side: str  # LONG | SHORT
    quantity: float  # absolute contract quantity
    entry_price: float
    mark_price: float
    leverage: float
    margin_type: str  # isolated | cross
    liquidation_price: float | None
    unrealized_pnl: float
    margin_used: float
    notional: float

    @classmethod
    def from_api(cls, raw: dict) -> "BinancePosition":
        amt = _f(raw.get("positionAmt"))
        mark = _f(raw.get("markPrice"))
        qty = abs(amt)
        liq = _f(raw.get("liquidationPrice"))
        return cls(
            symbol=raw.get("symbol", ""),
            side="LONG" if amt >= 0 else "SHORT",
            quantity=qty,
            entry_price=_f(raw.get("entryPrice")),
            mark_price=mark,
            leverage=_f(raw.get("leverage"), 1.0),
            margin_type=(raw.get("marginType") or "isolated").lower(),
            liquidation_price=liq if liq > 0 else None,
            unrealized_pnl=_f(raw.get("unRealizedProfit")),
            margin_used=_f(raw.get("isolatedMargin")) or _f(raw.get("initialMargin")),
            notional=abs(_f(raw.get("notional"))) or qty * mark,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BinanceOrder:
    order_id: int
    client_order_id: str
    symbol: str
    side: str  # BUY | SELL
    position_side: str
    type: str  # MARKET | LIMIT | STOP_MARKET | TAKE_PROFIT_MARKET | ...
    status: str  # NEW | PARTIALLY_FILLED | FILLED | CANCELED | REJECTED | EXPIRED
    price: float
    stop_price: float
    quantity: float
    executed_qty: float
    avg_price: float
    reduce_only: bool
    close_position: bool
    time: int | None = None

    @classmethod
    def from_api(cls, raw: dict) -> "BinanceOrder":
        return cls(
            order_id=int(raw.get("orderId", 0)),
            client_order_id=raw.get("clientOrderId", ""),
            symbol=raw.get("symbol", ""),
            side=raw.get("side", ""),
            position_side=raw.get("positionSide", "BOTH"),
            type=raw.get("type") or raw.get("origType", ""),
            status=raw.get("status", ""),
            price=_f(raw.get("price")),
            stop_price=_f(raw.get("stopPrice")),
            quantity=_f(raw.get("origQty")),
            executed_qty=_f(raw.get("executedQty")),
            avg_price=_f(raw.get("avgPrice")),
            reduce_only=bool(raw.get("reduceOnly")),
            close_position=bool(raw.get("closePosition")),
            time=raw.get("time") or raw.get("updateTime"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BinanceUserTrade:
    trade_id: int
    order_id: int
    symbol: str
    side: str
    price: float
    quantity: float
    realized_pnl: float
    commission: float
    commission_asset: str
    time: int | None = None

    @classmethod
    def from_api(cls, raw: dict) -> "BinanceUserTrade":
        return cls(
            trade_id=int(raw.get("id", 0)),
            order_id=int(raw.get("orderId", 0)),
            symbol=raw.get("symbol", ""),
            side=raw.get("side", ""),
            price=_f(raw.get("price")),
            quantity=_f(raw.get("qty")),
            realized_pnl=_f(raw.get("realizedPnl")),
            commission=_f(raw.get("commission")),
            commission_asset=raw.get("commissionAsset", ""),
            time=raw.get("time"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BinanceAccountSummary:
    total_wallet_balance: float
    available_balance: float
    total_margin_balance: float
    total_unrealized_pnl: float
    total_initial_margin: float
    total_maintenance_margin: float

    @classmethod
    def from_api(cls, raw: dict) -> "BinanceAccountSummary":
        return cls(
            total_wallet_balance=_f(raw.get("totalWalletBalance")),
            available_balance=_f(raw.get("availableBalance")),
            total_margin_balance=_f(raw.get("totalMarginBalance")),
            total_unrealized_pnl=_f(raw.get("totalUnrealizedProfit")),
            total_initial_margin=_f(raw.get("totalInitialMargin")),
            total_maintenance_margin=_f(raw.get("totalMaintMargin")),
        )

    def to_dict(self) -> dict:
        return asdict(self)
