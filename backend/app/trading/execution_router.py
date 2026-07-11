"""Unified execution interface (Phase 22).

The strategy engine and the trading API never talk to Binance directly -
they call this router, which resolves the current trading mode fresh on
every call and forwards to the right provider:

    PAPER               -> PaperExecutionProvider (existing simulated engine)
    BINANCE_TESTNET     -> BinanceExecutionProvider(testnet=True)
    BINANCE_LIVE        -> BinanceExecutionProvider(testnet=False)
    BINANCE_LIVE_LOCKED -> everything blocked with a clear reason

Real providers run every order through app/trading/real_risk_gate.py -
there is no parameter to skip it. The kill switch is checked here as well,
so even a provider bug can't place an order while it is active.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.core.security import create_internal_service_token
from app.db.models import ExchangePositionRow
from app.db.session import SessionLocal
from app.exchanges.binance_futures_client import BinanceFuturesClient
from app.execution.execution_engine import engine as paper_engine
from app.execution.order_router import OrderType
from app.trading import modes, real_risk_gate

PAPER_API = "http://127.0.0.1:8000"


@dataclass
class RouterResult:
    ok: bool
    mode: str
    action: str
    reason: str | None = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "mode": self.mode, "action": self.action, "reason": self.reason, **self.detail}


class PaperExecutionProvider:
    """Adapts the existing paper stack (execution engine + /api/paper) to
    the router interface. Close/risk edits go through the same internal
    HTTP endpoints the background loops already use, so every existing
    safety check and ledger side effect stays in force."""

    mode = modes.MODE_PAPER

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {create_internal_service_token()}"}

    async def open_position(self, symbol: str, side: str, notional_usdt: float, leverage: float = 1.0,
                            sl: float | None = None, tp: float | None = None, **kwargs) -> RouterResult:
        # Full decision provenance rides through to the paper ledger so a
        # bot-originated order journals identically whether the strategy
        # engine called the paper engine directly (pre-Phase 23) or through
        # this router.
        result = await paper_engine.submit_order(
            symbol=symbol,
            side=side,
            usdt_size=notional_usdt,
            order_type=kwargs.get("order_type", OrderType.MARKET),
            sl=sl,
            tp=tp,
            feature_id=kwargs.get("feature_id"),
            regime=kwargs.get("regime"),
            strategies=kwargs.get("strategies"),
            signal_time=kwargs.get("signal_time") or datetime.now(timezone.utc),
            open_positions=kwargs.get("open_positions", 0),
            equity=kwargs.get("equity"),
            timeframe=kwargs.get("timeframe"),
            decision_engine=kwargs.get("decision_engine"),
        )
        ok = result.status in ("FILLED", "PARTIAL")
        return RouterResult(
            ok=ok, mode=self.mode, action="open_position",
            reason=result.reason if not ok else None,
            detail={"status": result.status, "trade_id": result.trade_id,
                    "filled_qty": result.filled_qty, "avg_fill_price": result.avg_fill_price},
        )

    async def close_position(self, position_id: int | None = None, symbol: str | None = None,
                             quantity: float | None = None, **kwargs) -> RouterResult:
        if position_id is None:
            return RouterResult(ok=False, mode=self.mode, action="close_position",
                                reason="position_id is required in paper mode")
        async with httpx.AsyncClient(timeout=15, headers=self._headers()) as client:
            r = await client.post(
                f"{PAPER_API}/api/paper/positions/{position_id}/close",
                json={"quantity": quantity} if quantity is not None else {},
            )
            data = r.json()
        return RouterResult(ok=r.status_code == 200, mode=self.mode, action="close_position",
                            reason=data.get("detail") if r.status_code != 200 else None, detail=data if r.status_code == 200 else {})

    async def update_stop_loss(self, position_id: int, stop_loss: float | None, **kwargs) -> RouterResult:
        return await self._patch_risk(position_id, {"stop_loss": stop_loss})

    async def update_take_profit(self, position_id: int, take_profit: float | None, **kwargs) -> RouterResult:
        return await self._patch_risk(position_id, {"take_profit": take_profit})

    async def _patch_risk(self, position_id: int, patch: dict) -> RouterResult:
        async with httpx.AsyncClient(timeout=15, headers=self._headers()) as client:
            r = await client.patch(f"{PAPER_API}/api/paper/positions/{position_id}/risk", json=patch)
            data = r.json()
        return RouterResult(ok=r.status_code == 200, mode=self.mode, action="update_risk",
                            reason=data.get("detail") if r.status_code != 200 else None,
                            detail=data if r.status_code == 200 else {})

    async def sync_positions(self) -> RouterResult:
        return RouterResult(ok=True, mode=self.mode, action="sync_positions",
                            detail={"synced": 0, "note": "paper ledger is local - nothing to sync"})

    async def sync_orders(self) -> RouterResult:
        return RouterResult(ok=True, mode=self.mode, action="sync_orders",
                            detail={"synced": 0, "note": "paper ledger is local - nothing to sync"})

    async def cancel_order(self, symbol: str, order_id: int, **kwargs) -> RouterResult:
        return RouterResult(ok=False, mode=self.mode, action="cancel_order",
                            reason="paper mode has no resting exchange orders to cancel")

    async def cancel_all_orders(self, symbol: str | None = None, **kwargs) -> RouterResult:
        return RouterResult(ok=True, mode=self.mode, action="cancel_all_orders",
                            detail={"canceled": 0, "note": "paper mode has no resting exchange orders"})


class BinanceExecutionProvider:
    """Real order flow against Binance Futures (testnet or live). Every
    open runs the full real_risk_gate checklist; TP/SL are real reduce-only
    orders, never simulated."""

    def __init__(self, testnet: bool):
        self.testnet = testnet
        self.mode = modes.MODE_TESTNET if testnet else modes.MODE_LIVE
        self._client: BinanceFuturesClient | None = None

    @property
    def client(self) -> BinanceFuturesClient:
        if self._client is None:
            self._client = BinanceFuturesClient(testnet=self.testnet)
        return self._client

    def _result(self, ok: bool, action: str, reason: str | None = None, **detail) -> RouterResult:
        return RouterResult(ok=ok, mode=self.mode, action=action, reason=reason, detail=detail)

    async def open_position(self, symbol: str, side: str, notional_usdt: float, leverage: float | None = None,
                            sl: float | None = None, tp: float | None = None, confidence: float | None = None,
                            data_reliable: bool | None = None, spread_pct: float | None = None,
                            open_positions: int | None = None, clamp_to_max: bool = False,
                            **kwargs) -> RouterResult:
        symbol = symbol.upper()
        side = side.upper()
        leverage = leverage if leverage and leverage > 0 else settings.binance_default_leverage
        leverage = min(leverage, settings.binance_max_leverage)

        # clamp_to_max is set ONLY by the strategy engine's bot intent: its
        # paper position sizing (max_position_size_usd, often $1000) would
        # trip the real max-notional gate on every cycle, so a bot order is
        # capped at the configured per-trade notional instead of being
        # permanently blocked. Any other oversized order is still BLOCKED by
        # the gate below. The clamp is audited.
        if clamp_to_max and notional_usdt > settings.binance_max_notional_per_trade:
            modes.audit("order_notional_clamped", symbol=symbol,
                        detail={"requested": notional_usdt, "clamped_to": settings.binance_max_notional_per_trade})
            notional_usdt = settings.binance_max_notional_per_trade

        modes.audit("order_requested", symbol=symbol,
                    detail={"side": side, "notional": notional_usdt, "leverage": leverage, "sl": sl, "tp": tp})

        # max_open_positions must count REAL exchange positions, not the
        # paper count the strategy engine happens to know about.
        try:
            open_positions = len(await self.client.get_positions())
        except Exception:
            pass  # keep the caller-provided count; the gate re-verifies reachability anyway

        gate = await real_risk_gate.evaluate_real_order(
            symbol=symbol, side=side, notional_usdt=notional_usdt, leverage=leverage,
            client=self.client, confidence=confidence, data_reliable=data_reliable,
            spread_pct=spread_pct, open_positions=open_positions,
        )
        if not gate.allowed:
            return self._result(False, "open_position", reason=gate.reason, checks=gate.checks)

        try:
            mark = await self.client.get_mark_price(symbol)
            qty = _round_qty(symbol, notional_usdt / mark)
            if qty <= 0:
                return self._result(False, "open_position",
                                    reason=f"Notional ${notional_usdt:.2f} is below the minimum order size for {symbol}")

            await self.client.set_margin_type(symbol, "ISOLATED")
            await self.client.set_leverage(symbol, int(leverage))

            order = await self.client.place_market_order(
                symbol=symbol, side="BUY" if side == "LONG" else "SELL", quantity=qty,
            )
            modes.audit("order_accepted", symbol=symbol,
                        detail={"order_id": order.order_id, "qty": qty, "side": side})

            sl_order_id = tp_order_id = None
            if sl is not None:
                sl_order = await self.client.place_stop_loss(symbol, side, sl)
                sl_order_id = sl_order.order_id
            if tp is not None:
                tp_order = await self.client.place_take_profit(symbol, side, tp)
                tp_order_id = tp_order.order_id

            modes.audit("order_filled", symbol=symbol, detail={
                "order_id": order.order_id, "status": order.status,
                "executed_qty": order.executed_qty, "avg_price": order.avg_price,
                "sl_order_id": sl_order_id, "tp_order_id": tp_order_id,
            })
            self._record_bot_trade(
                action="open", order=order, side=side, notional=notional_usdt,
                leverage=leverage, sl_order_id=sl_order_id, tp_order_id=tp_order_id,
                sl=sl, tp=tp, gate_checks=gate.checks, **kwargs,
            )
            await self.sync_positions()
            return self._result(True, "open_position", order=order.to_dict(),
                                sl_order_id=sl_order_id, tp_order_id=tp_order_id)
        except Exception as e:
            modes.audit("order_rejected", symbol=symbol, detail={"error": _safe_error(e)})
            return self._result(False, "open_position", reason=_safe_error(e))

    def _record_bot_trade(self, action: str, order, side: str, notional: float | None = None,
                          leverage: float | None = None, sl_order_id: int | None = None,
                          tp_order_id: int | None = None, sl: float | None = None,
                          tp: float | None = None, gate_checks: list | None = None, **kwargs) -> None:
        """Journal one accepted real order into binance_bot_trades. Best
        effort - a journaling failure must never unwind a live fill."""
        from app.db.models import BinanceBotTrade

        decision_engine = kwargs.get("decision_engine") or {}
        active_model = decision_engine.get("active_model") or {}
        reasons = decision_engine.get("top_reasons")
        db = SessionLocal()
        try:
            db.add(BinanceBotTrade(
                mode=self.mode,
                action=action,
                order_id=order.order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=side,
                order_side=order.side,
                order_type=order.type,
                quantity=order.quantity,
                avg_fill_price=order.avg_price or None,
                status=order.status,
                reduce_only=order.reduce_only,
                notional=notional,
                leverage=leverage,
                sl_order_id=sl_order_id,
                tp_order_id=tp_order_id,
                stop_loss=sl,
                take_profit=tp,
                label="BOT_TRADE",
                confidence=kwargs.get("confidence") or decision_engine.get("final_confidence"),
                strategy=kwargs.get("strategy") or decision_engine.get("strategy_used"),
                model=kwargs.get("model") or active_model.get("model_type"),
                decision_reason="; ".join(reasons) if isinstance(reasons, list) else kwargs.get("reason"),
                risk_gate=gate_checks,
            ))
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    async def close_position(self, position_id: int | None = None, symbol: str | None = None,
                             quantity: float | None = None, **kwargs) -> RouterResult:
        if not symbol:
            symbol = _symbol_for_local_position(position_id)
            if not symbol:
                return self._result(False, "close_position", reason="symbol (or a synced position id) is required")
        symbol = symbol.upper()
        try:
            positions = await self.client.get_positions(symbol)
            if not positions:
                return self._result(False, "close_position", reason=f"No open {symbol} position on Binance")
            pos = positions[0]
            qty = _round_qty(symbol, quantity if quantity and quantity > 0 else pos.quantity)
            order = await self.client.place_market_order(
                symbol=symbol, side="SELL" if pos.side == "LONG" else "BUY",
                quantity=qty, reduce_only=True,
            )
            modes.audit("position_closed", symbol=symbol,
                        detail={"order_id": order.order_id, "qty": qty, "side": pos.side})
            self._record_bot_trade(action="close", order=order, side=pos.side, **kwargs)
            await self.sync_positions()
            return self._result(True, "close_position", order=order.to_dict())
        except Exception as e:
            modes.audit("order_rejected", symbol=symbol, detail={"action": "close", "error": _safe_error(e)})
            return self._result(False, "close_position", reason=_safe_error(e))

    async def update_stop_loss(self, position_id: int | None = None, stop_loss: float | None = None,
                               symbol: str | None = None, **kwargs) -> RouterResult:
        return await self._replace_protective_order(symbol, position_id, "stop_loss", stop_loss)

    async def update_take_profit(self, position_id: int | None = None, take_profit: float | None = None,
                                 symbol: str | None = None, **kwargs) -> RouterResult:
        return await self._replace_protective_order(symbol, position_id, "take_profit", take_profit)

    async def _replace_protective_order(self, symbol: str | None, position_id: int | None,
                                        kind: str, price: float | None) -> RouterResult:
        """Cancel-then-replace one real TP/SL order. Local state is updated
        only after the exchange confirms the new order."""
        if not symbol:
            symbol = _symbol_for_local_position(position_id)
            if not symbol:
                return self._result(False, f"update_{kind}", reason="symbol (or a synced position id) is required")
        symbol = symbol.upper()
        order_type = "STOP_MARKET" if kind == "stop_loss" else "TAKE_PROFIT_MARKET"
        try:
            positions = await self.client.get_positions(symbol)
            if not positions:
                return self._result(False, f"update_{kind}", reason=f"No open {symbol} position on Binance")
            pos = positions[0]

            from app.trading import margin as margin_calc
            if price is not None:
                margin_calc.validate_risk_levels(
                    pos.side, pos.mark_price,
                    price if kind == "stop_loss" else None,
                    price if kind == "take_profit" else None,
                )

            for order in await self.client.get_open_orders(symbol):
                if order.type == order_type:
                    await self.client.cancel_order(symbol, order.order_id)
                    modes.audit("protective_order_canceled", symbol=symbol,
                                detail={"kind": kind, "order_id": order.order_id})

            new_order_id = None
            if price is not None:
                place = self.client.place_stop_loss if kind == "stop_loss" else self.client.place_take_profit
                new_order = await place(symbol, pos.side, price)
                new_order_id = new_order.order_id

            modes.audit("tp_sl_updated", symbol=symbol,
                        detail={"kind": kind, "price": price, "order_id": new_order_id})
            _store_protective_order_id(self.mode, symbol, kind, new_order_id)
            return self._result(True, f"update_{kind}", order_id=new_order_id, price=price)
        except Exception as e:
            return self._result(False, f"update_{kind}", reason=_safe_error(e))

    async def sync_positions(self) -> RouterResult:
        """Mirror Binance's open positions into exchange_positions. Binance
        is the source of truth; local rows are replaced wholesale."""
        try:
            positions = await self.client.get_positions()
        except Exception as e:
            return self._result(False, "sync_positions", reason=_safe_error(e))

        db = SessionLocal()
        try:
            existing = {
                (row.symbol): row
                for row in db.query(ExchangePositionRow).filter(ExchangePositionRow.mode == self.mode).all()
            }
            now = datetime.now(timezone.utc)
            seen = set()
            for p in positions:
                seen.add(p.symbol)
                row = existing.get(p.symbol) or ExchangePositionRow(mode=self.mode, symbol=p.symbol)
                row.side = p.side
                row.quantity = p.quantity
                row.entry_price = p.entry_price
                row.mark_price = p.mark_price
                row.leverage = p.leverage
                row.margin_type = p.margin_type
                row.liquidation_price = p.liquidation_price
                row.unrealized_pnl = p.unrealized_pnl
                row.margin_used = p.margin_used
                row.notional = p.notional
                row.updated_at = now
                db.add(row)
            for symbol, row in existing.items():
                if symbol not in seen:
                    db.delete(row)
            db.commit()
        finally:
            db.close()

        modes.audit("positions_synced", detail={"count": len(positions)})
        return self._result(True, "sync_positions", synced=len(positions))

    async def sync_orders(self) -> RouterResult:
        try:
            orders = await self.client.get_open_orders()
            return self._result(True, "sync_orders", synced=len(orders),
                                orders=[o.to_dict() for o in orders])
        except Exception as e:
            return self._result(False, "sync_orders", reason=_safe_error(e))

    async def cancel_order(self, symbol: str, order_id: int, **kwargs) -> RouterResult:
        try:
            order = await self.client.cancel_order(symbol, order_id)
            modes.audit("order_canceled", symbol=symbol.upper(), detail={"order_id": order_id})
            return self._result(True, "cancel_order", order=order.to_dict())
        except Exception as e:
            return self._result(False, "cancel_order", reason=_safe_error(e))

    async def cancel_all_orders(self, symbol: str | None = None, **kwargs) -> RouterResult:
        symbols = [symbol.upper()] if symbol else list(settings.binance_allowed_symbols)
        canceled: list[str] = []
        errors: list[str] = []
        for sym in symbols:
            try:
                await self.client.cancel_all_orders(sym)
                canceled.append(sym)
            except Exception as e:
                errors.append(f"{sym}: {_safe_error(e)}")
        modes.audit("orders_canceled_all", detail={"symbols": canceled, "errors": errors})
        return self._result(not errors, "cancel_all_orders",
                            reason="; ".join(errors) if errors else None, canceled_symbols=canceled)


# Binance rejects quantities with more precision than the symbol's step
# size. A static table for the two supported symbols keeps this dependency-
# free; unknown symbols fall back to 3 decimals (safe for most USDT pairs).
_QTY_DECIMALS = {"BTCUSDT": 3, "ETHUSDT": 3}


def _round_qty(symbol: str, qty: float) -> float:
    import math
    decimals = _QTY_DECIMALS.get(symbol.upper(), 3)
    return math.floor(qty * 10**decimals) / 10**decimals


def _safe_error(e: Exception) -> str:
    """Error text safe for the UI/audit log: message + type only, never a
    raw request/response dump (which could include signed URLs)."""
    from app.exchanges.binance_errors import BinanceError
    from app.trading.margin import RiskValidationError
    if isinstance(e, (BinanceError, RiskValidationError)):
        return getattr(e, "message", str(e))
    return f"{type(e).__name__}: {e}"


def _symbol_for_local_position(position_id: int | None) -> str | None:
    if position_id is None:
        return None
    db = SessionLocal()
    try:
        row = db.get(ExchangePositionRow, position_id)
        return row.symbol if row else None
    finally:
        db.close()


def _store_protective_order_id(mode: str, symbol: str, kind: str, order_id: int | None) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(ExchangePositionRow)
            .filter(ExchangePositionRow.mode == mode, ExchangePositionRow.symbol == symbol)
            .first()
        )
        if row:
            if kind == "stop_loss":
                row.stop_loss_order_id = str(order_id) if order_id else None
            else:
                row.take_profit_order_id = str(order_id) if order_id else None
            row.updated_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


class ExecutionRouter:
    """Mode-resolving facade. Resolution happens per-call so a mode change
    (or kill switch) applies to the very next order with no restart."""

    def __init__(self):
        self._paper = PaperExecutionProvider()
        self._testnet = BinanceExecutionProvider(testnet=True)
        self._live: BinanceExecutionProvider | None = None

    def provider(self):
        mode = modes.effective_mode()
        if mode == modes.MODE_TESTNET:
            return self._testnet
        if mode == modes.MODE_LIVE:
            # constructed lazily: BinanceFuturesClient(testnet=False) refuses
            # to exist unless BINANCE_LIVE_ENABLED=true
            if self._live is None:
                self._live = BinanceExecutionProvider(testnet=False)
            return self._live
        return self._paper

    def _blocked(self, action: str) -> RouterResult | None:
        if modes.kill_switch_active():
            return RouterResult(ok=False, mode=modes.effective_mode(), action=action,
                                reason="Kill switch active - all trading halted")
        if modes.effective_mode() == modes.MODE_LIVE_LOCKED:
            return RouterResult(ok=False, mode=modes.MODE_LIVE_LOCKED, action=action,
                                reason="Live trading locked - complete the unlock confirmation first")
        return None

    async def open_position(self, **kwargs) -> RouterResult:
        return self._blocked("open_position") or await self.provider().open_position(**kwargs)

    async def close_position(self, **kwargs) -> RouterResult:
        # Closing/canceling stays allowed while live is merely locked, but a
        # LOCKED mode has no real provider - route those to paper only if
        # they are paper positions. For LOCKED, closing real positions is a
        # manual exchange action; report that honestly.
        if modes.effective_mode() == modes.MODE_LIVE_LOCKED:
            return RouterResult(ok=False, mode=modes.MODE_LIVE_LOCKED, action="close_position",
                                reason="Live trading locked - manage live positions directly on Binance")
        return await self.provider().close_position(**kwargs)

    async def update_stop_loss(self, **kwargs) -> RouterResult:
        blocked = self._blocked("update_stop_loss")
        return blocked or await self.provider().update_stop_loss(**kwargs)

    async def update_take_profit(self, **kwargs) -> RouterResult:
        blocked = self._blocked("update_take_profit")
        return blocked or await self.provider().update_take_profit(**kwargs)

    async def sync_positions(self) -> RouterResult:
        if modes.effective_mode() == modes.MODE_LIVE_LOCKED:
            return RouterResult(ok=False, mode=modes.MODE_LIVE_LOCKED, action="sync_positions",
                                reason="Live trading locked")
        return await self.provider().sync_positions()

    async def sync_orders(self) -> RouterResult:
        if modes.effective_mode() == modes.MODE_LIVE_LOCKED:
            return RouterResult(ok=False, mode=modes.MODE_LIVE_LOCKED, action="sync_orders",
                                reason="Live trading locked")
        return await self.provider().sync_orders()

    async def cancel_order(self, **kwargs) -> RouterResult:
        # cancel is a de-risking action: allowed even when locked, IF a real
        # provider can be built (testnet). Locked-live cannot construct one.
        if modes.effective_mode() == modes.MODE_LIVE_LOCKED:
            return RouterResult(ok=False, mode=modes.MODE_LIVE_LOCKED, action="cancel_order",
                                reason="Live trading locked - cancel orders directly on Binance")
        return await self.provider().cancel_order(**kwargs)

    async def cancel_all_orders(self, **kwargs) -> RouterResult:
        if modes.effective_mode() == modes.MODE_LIVE_LOCKED:
            return RouterResult(ok=False, mode=modes.MODE_LIVE_LOCKED, action="cancel_all_orders",
                                reason="Live trading locked - cancel orders directly on Binance")
        return await self.provider().cancel_all_orders(**kwargs)


router = ExecutionRouter()
