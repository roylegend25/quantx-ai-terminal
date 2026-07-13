"""Unified execution interface (Phase 22).

The strategy engine and the trading API never talk to Binance directly -
they call this router, which resolves the current trading mode fresh on
every call and forwards to the right provider:

    PAPER               -> PaperExecutionProvider (existing simulated engine)
    BINANCE_TESTNET     -> BinanceExecutionProvider(testnet=True)
    BINANCE_LIVE        -> BinanceExecutionProvider(testnet=False)
    BINANCE_LIVE_LOCKED -> everything blocked with a clear reason

Cancel/cancel-all are the one documented exception (see
ExecutionRouter._cancel_provider): they are de-risking safety actions, not
order placement, so they reach the real account in every mode - including
PAPER and LIVE_LOCKED - as long as one is actually configured.

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
from app.exchanges.binance_futures_client import BinanceFuturesClient, LiveTradingLocked
from app.execution.execution_engine import engine as paper_engine
from app.execution.order_router import OrderType
from app.trading import modes, protection, protection_provider, real_risk_gate
from app.trading.execution_pipeline import (
    STAGE_BINANCE_VALIDATION,
    STAGE_CHAMPION_MODEL,
    STAGE_ENTRY_ORDER_ACCEPTED,
    STAGE_ENTRY_ORDER_SUBMITTED,
    STAGE_POSITION_SIZING,
    STAGE_PROTECTION_CONFIRMED,
    STAGE_PROTECTION_PROVIDER_SELECTED,
    STAGE_PROTECTIVE_SL_SUBMITTED,
    STAGE_PROTECTIVE_TP_SUBMITTED,
    STAGE_QUANTITY_CALCULATION,
    STAGE_RISK_GATE,
    STAGE_TRADE_ACTIVE,
    STATUS_FAILED,
    STATUS_SUCCESS,
    STATUS_WAITING,
    PipelineRecorder,
    classify_binance_error,
    classify_gate_failure,
)

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

    async def cancel_order(self, symbol: str, order_id: int | None = None, **kwargs) -> RouterResult:
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
                            is_test: bool = False, **kwargs) -> RouterResult:
        symbol = symbol.upper()
        side = side.upper()
        leverage = leverage if leverage and leverage > 0 else settings.binance_default_leverage
        leverage = min(leverage, settings.binance_max_leverage)

        # Phase 25 execution transparency: one BinanceExecutionAttempt row
        # per call, stage-by-stage, regardless of outcome - see
        # app.trading.execution_pipeline. This is purely additive
        # instrumentation; every existing audit call, control-flow branch
        # and RouterResult below is unchanged.
        decision_engine = kwargs.get("decision_engine") or {}
        recorder = PipelineRecorder(
            mode=self.mode, symbol=symbol, side=side, is_test=is_test,
            confidence=confidence, requested_notional=notional_usdt, leverage=leverage,
        )
        has_model = bool(decision_engine.get("active_model"))
        recorder.stage(
            STAGE_CHAMPION_MODEL, STATUS_SUCCESS if has_model else STATUS_WAITING,
            None if has_model else "No Champion model metadata supplied with this order",
        )

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
        recorder.stage(
            STAGE_POSITION_SIZING, STATUS_SUCCESS,
            None if notional_usdt == recorder.requested_notional
            else f"Clamped to configured max per-trade notional (${notional_usdt:.2f})",
        )

        modes.audit("order_requested", symbol=symbol,
                    detail={"side": side, "notional": notional_usdt, "leverage": leverage, "sl": sl, "tp": tp})

        # max_open_positions must count REAL exchange positions, not the
        # paper count the strategy engine happens to know about.
        existing_position_on_symbol = False
        try:
            live_positions = await self.client.get_positions()
            open_positions = len(live_positions)
            existing_position_on_symbol = any(p.symbol == symbol for p in live_positions)
        except Exception:
            pass  # keep the caller-provided count; the gate re-verifies reachability anyway

        gate = await real_risk_gate.evaluate_real_order(
            symbol=symbol, side=side, notional_usdt=notional_usdt, leverage=leverage,
            client=self.client, confidence=confidence, data_reliable=data_reliable,
            spread_pct=spread_pct, open_positions=open_positions, sl=sl, tp=tp,
            existing_position_on_symbol=existing_position_on_symbol,
        )
        if not gate.allowed:
            failing = next((c for c in gate.checks if not c.get("passed")), None)
            recorder.stage(STAGE_RISK_GATE, STATUS_FAILED, classify_gate_failure(failing and failing.get("check")))
            recorder.finish("failed", reason=gate.reason, exchange_response={"checks": gate.checks})
            return self._result(False, "open_position", reason=gate.reason, checks=gate.checks)
        recorder.stage(STAGE_RISK_GATE, STATUS_SUCCESS)

        try:
            mark = await self.client.get_mark_price(symbol)
        except Exception as e:
            reason = _safe_error(e)
            recorder.stage(STAGE_QUANTITY_CALCULATION, STATUS_FAILED, classify_binance_error(e))
            recorder.finish("failed", reason=reason)
            modes.audit("order_rejected", symbol=symbol, detail={"error": reason})
            return self._result(False, "open_position", reason=reason)

        raw_qty = notional_usdt / mark
        qty = _round_qty(symbol, raw_qty)
        recorder.requested_quantity = round(raw_qty, 8)
        recorder.adjusted_quantity = qty
        recorder.margin_required = round(notional_usdt / max(leverage, 1.0), 2)
        if qty <= 0:
            reason = f"Notional ${notional_usdt:.2f} is below the minimum order size for {symbol}"
            recorder.stage(STAGE_QUANTITY_CALCULATION, STATUS_FAILED, "Quantity below minimum")
            recorder.finish("failed", reason=reason)
            return self._result(False, "open_position", reason=reason)
        recorder.stage(STAGE_QUANTITY_CALCULATION, STATUS_SUCCESS)

        try:
            # Margin type / leverage are one-time-per-symbol account
            # settings, not something to re-assert on every order - Binance
            # itself refuses to change either while a position (or a
            # resting order) already exists for the symbol, which is what
            # actually produced "Position side cannot be changed if there
            # exists open orders." in production every cycle the bot
            # re-signaled an already-open symbol. The risk gate above now
            # blocks that case before this point is ever reached in the
            # normal flow; this skip is defense-in-depth for any other
            # caller of open_position (e.g. is_test) - never fail a
            # perfectly fine entry over a call that was never going to
            # succeed anyway.
            if not existing_position_on_symbol:
                await self.client.set_margin_type(symbol, "ISOLATED")
                await self.client.set_leverage(symbol, int(leverage))
        except Exception as e:
            reason = _safe_error(e)
            recorder.stage(STAGE_BINANCE_VALIDATION, STATUS_FAILED, classify_binance_error(e))
            recorder.finish("failed", reason=reason)
            modes.audit("order_rejected", symbol=symbol, detail={"error": reason})
            return self._result(False, "open_position", reason=reason)
        recorder.stage(STAGE_BINANCE_VALIDATION, STATUS_SUCCESS)

        try:
            order = await self.client.place_market_order(
                symbol=symbol, side="BUY" if side == "LONG" else "SELL", quantity=qty,
            )
        except Exception as e:
            reason = _safe_error(e)
            recorder.stage(STAGE_ENTRY_ORDER_SUBMITTED, STATUS_FAILED, classify_binance_error(e))
            recorder.finish("failed", reason=reason)
            modes.audit("order_rejected", symbol=symbol, detail={"error": reason})
            return self._result(False, "open_position", reason=reason)
        recorder.stage(STAGE_ENTRY_ORDER_SUBMITTED, STATUS_SUCCESS)
        recorder.order_id = order.order_id

        modes.audit("order_accepted", symbol=symbol,
                    detail={"order_id": order.order_id, "qty": qty, "side": side})
        recorder.stage(STAGE_ENTRY_ORDER_ACCEPTED, STATUS_SUCCESS)

        # Phase 27: the entry filling is NOT a complete trade - a naked real
        # position is unsafe. Protective TP/SL are attempted next and
        # verified against Binance's own open orders (never trusted from
        # the placement call's return value alone) before this attempt is
        # ever considered done. A failure here still journals the trade
        # (fixing the prior bug where a protection failure silently skipped
        # _record_bot_trade too, leaving an open position with no local
        # record at all) and, if configured, closes the naked position.
        try:
            fill_price = order.avg_price or mark
            try:
                from app.trading.margin import validate_risk_levels
                if sl is not None or tp is not None:
                    validate_risk_levels(side, fill_price, sl, tp)
            except Exception as e:
                sl = tp = None  # invalid levels are never sent to Binance
                invalid_reason = str(e)
            else:
                invalid_reason = None

            try:
                hedge_mode = await self.client.get_position_mode()
            except Exception:
                hedge_mode = False

            # Phase 26 Algo Order Provider: classic first (or whatever this
            # mode is already known to need - app.trading.protection_capability),
            # auto-migrating to the Algo Order API on the first -4120. Every
            # order after that first migration for this mode skips classic
            # entirely. See app.trading.protection_provider.
            recorder.stage(STAGE_PROTECTION_PROVIDER_SELECTED, STATUS_SUCCESS,
                            f"provider={protection_provider.capability.get_provider(self.mode) or protection_provider.CLASSIC}")
            protection_result = await protection_provider.place_protection(
                client=self.client, mode=self.mode, symbol=symbol, position_side=side,
                tp=tp, sl=sl, quantity=qty, hedge_mode=hedge_mode,
            )
            provider_used = protection_result.provider_used
            tp_order_id = sl_order_id = tp_algo_id = sl_algo_id = None
            if provider_used == protection_provider.ALGO:
                tp_algo_id = protection_result.tp.order_id
                sl_algo_id = protection_result.sl.order_id
            else:
                tp_order_id = protection_result.tp.order_id
                sl_order_id = protection_result.sl.order_id
            protection_failed_reason = invalid_reason
            if not protection_result.tp.ok:
                protection_failed_reason = protection_failed_reason or protection_result.tp.error
            if not protection_result.sl.ok:
                protection_failed_reason = protection_failed_reason or protection_result.sl.error
            recorder.stage(STAGE_PROTECTIVE_TP_SUBMITTED,
                            STATUS_SUCCESS if protection_result.tp.ok else STATUS_FAILED,
                            None if protection_result.tp.ok else (protection_result.tp.error or "No take-profit computed for this entry"))
            recorder.stage(STAGE_PROTECTIVE_SL_SUBMITTED,
                            STATUS_SUCCESS if protection_result.sl.ok else STATUS_FAILED,
                            None if protection_result.sl.ok else (protection_result.sl.error or "No stop-loss computed for this entry"))

            # Ground truth: verify against Binance's own open orders (classic)
            # AND, when the algo provider placed this leg, the specific algo
            # id queried individually (see protection.resolve_protection) -
            # never just trust that the placement calls above returned an id.
            protection_confirmed_at = None
            verification_result = None
            try:
                verified = await protection.resolve_protection(
                    self.client, symbol, tp_algo_id=tp_algo_id, sl_algo_id=sl_algo_id,
                )
                sl_order_id = verified.sl_order_id or sl_order_id
                tp_order_id = verified.tp_order_id or tp_order_id
                protection_status = verified.status
                verification_result = protection_status
                if protection_status == protection.PROTECTED:
                    protection_confirmed_at = datetime.now(timezone.utc)
                    recorder.stage(STAGE_PROTECTION_CONFIRMED, STATUS_SUCCESS)
                else:
                    protection_failed_reason = protection_failed_reason or f"Verification found {protection_status} on Binance"
                    recorder.stage(STAGE_PROTECTION_CONFIRMED, STATUS_FAILED, protection_failed_reason)
            except Exception as e:
                protection_status = protection.MISSING_BOTH
                verification_result = "verification_error"
                protection_failed_reason = protection_failed_reason or _safe_error(e)
                recorder.stage(STAGE_PROTECTION_CONFIRMED, STATUS_FAILED, classify_binance_error(e))

            modes.audit("order_filled", symbol=symbol, detail={
                "order_id": order.order_id, "status": order.status,
                "executed_qty": order.executed_qty, "avg_price": order.avg_price,
                "sl_order_id": sl_order_id, "tp_order_id": tp_order_id,
                "protection_status": protection_status, "protection_provider": provider_used,
            })

            # Journal unconditionally - protection outcome, good or bad, is
            # itself part of the trade record, not a reason to skip it.
            self._record_bot_trade(
                action="open", order=order, side=side, notional=notional_usdt,
                leverage=leverage, sl_order_id=sl_order_id, tp_order_id=tp_order_id,
                sl=sl, tp=tp, gate_checks=gate.checks,
                protection_status=protection_status, protection_failed_reason=protection_failed_reason,
                protection_confirmed_at=protection_confirmed_at, entry_order_id=order.order_id,
                provider_used=provider_used, provider_response=protection_result.to_dict(),
                provider_latency_ms=protection_result.latency_ms, verification_result=verification_result,
                **kwargs,
            )

            closed_due_to_protection_failure = False
            if protection_status != protection.PROTECTED and settings.binance_close_if_protection_fails:
                close_result = await self.close_position(symbol=symbol)
                closed_due_to_protection_failure = close_result.ok
                modes.audit("position_closed_due_to_unprotected", symbol=symbol,
                            detail={"reason": protection_failed_reason, "close_ok": close_result.ok})

            await self.sync_positions()
            if not closed_due_to_protection_failure:
                _store_protection_metadata(self.mode, symbol, provider_used, tp_order_id, sl_order_id, tp_algo_id, sl_algo_id,
                                           protection_status=protection_status)

            if protection_status != protection.PROTECTED:
                reason = f"Entry filled but protection failed ({protection_status}): {protection_failed_reason}"
                if closed_due_to_protection_failure:
                    reason += " - position closed"
                recorder.finish("PROTECTION_FAILED", reason=reason, exchange_response=order.to_dict())
                return self._result(
                    False, "open_position", reason=reason, order=order.to_dict(),
                    sl_order_id=sl_order_id, tp_order_id=tp_order_id,
                    protection_status=protection_status, closed=closed_due_to_protection_failure,
                    protection_provider=provider_used,
                )

            recorder.stage(STAGE_TRADE_ACTIVE, STATUS_SUCCESS)
            recorder.finish("order_accepted", exchange_response=order.to_dict())
            return self._result(True, "open_position", order=order.to_dict(),
                                sl_order_id=sl_order_id, tp_order_id=tp_order_id, protection_status=protection_status,
                                protection_provider=provider_used)
        except Exception as e:
            reason = _safe_error(e)
            modes.audit("order_rejected", symbol=symbol, detail={"error": reason})
            recorder.finish("PROTECTION_FAILED", reason=f"Order accepted but a post-fill step failed: {reason}",
                            exchange_response=order.to_dict())
            return self._result(False, "open_position", reason=reason)

    def _record_bot_trade(self, action: str, order, side: str, notional: float | None = None,
                          leverage: float | None = None, sl_order_id: int | None = None,
                          tp_order_id: int | None = None, sl: float | None = None,
                          tp: float | None = None, gate_checks: list | None = None,
                          protection_status: str | None = None, protection_failed_reason: str | None = None,
                          protection_confirmed_at=None, entry_order_id: int | None = None,
                          exit_order_id: int | None = None, exit_reason: str | None = None,
                          provider_used: str | None = None, provider_response: dict | None = None,
                          provider_latency_ms: float | None = None, verification_result: str | None = None,
                          repair_attempts: int = 0,
                          **kwargs) -> None:
        """Journal one real order into binance_bot_trades - called
        unconditionally for every accepted entry, regardless of whether its
        protective TP/SL succeeded (see open_position: this used to be
        skipped whenever TP/SL placement raised, which is how the position
        that prompted this fix went unprotected AND unjournaled). Best
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
                entry_order_id=entry_order_id,
                protection_status=protection_status,
                protection_failed_reason=protection_failed_reason,
                protection_confirmed_at=protection_confirmed_at,
                exit_order_id=exit_order_id,
                exit_reason=exit_reason,
                provider_used=provider_used,
                provider_response=provider_response,
                provider_latency_ms=provider_latency_ms,
                verification_result=verification_result,
                repair_attempts=repair_attempts,
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
            self._record_bot_trade(action="close", order=order, side=pos.side, exit_order_id=order.order_id, **kwargs)

            # No position left to protect - any resting TP/SL for this
            # symbol is now an orphan reduce-only order that would otherwise
            # sit forever. Cancel-all is symbol-scoped and safe: a fresh
            # position on the same symbol only exists after this call.
            canceled_orphans: list[int] = []
            try:
                for o in await self.client.get_open_orders(symbol):
                    if o.reduce_only or o.close_position:
                        await self.client.cancel_order(symbol, o.order_id)
                        canceled_orphans.append(o.order_id)
                # Algo orders (Phase 26 Algo Order Provider) never show up in
                # get_open_orders - clean up any tracked ids separately.
                for algo_id in (_tracked_algo_id(self.mode, symbol, "tp"), _tracked_algo_id(self.mode, symbol, "sl")):
                    if not algo_id:
                        continue
                    try:
                        await protection_provider.cancel_leg(self.client, symbol, algo_id, protection_provider.ALGO)
                        canceled_orphans.append(algo_id)
                    except Exception:
                        pass  # already gone (triggered/expired) - not a failure
                if canceled_orphans:
                    modes.audit("protective_order_canceled", symbol=symbol,
                                detail={"reason": "orphaned after close", "order_ids": canceled_orphans})
            except Exception:
                pass  # best-effort cleanup - the position close itself already succeeded

            await self.sync_positions()
            return self._result(True, "close_position", order=order.to_dict(), canceled_orphan_orders=canceled_orphans)
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
        """Cancel-then-replace one real TP/SL order, through whichever
        provider (classic or algo - Phase 26 Algo Order Provider) this
        position's leg is currently on. Local state is updated only after
        the exchange confirms the new order. Binance has no in-place
        "modify" for either surface, so both this and the classic-only
        predecessor already worked as cancel-then-replace - the only new
        behavior is routing the cancel to the right surface."""
        if not symbol:
            symbol = _symbol_for_local_position(position_id)
            if not symbol:
                return self._result(False, f"update_{kind}", reason="symbol (or a synced position id) is required")
        symbol = symbol.upper()
        leg = "sl" if kind == "stop_loss" else "tp"
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

            # Cancel the existing leg wherever it actually lives: a classic
            # resting order (scanned from get_open_orders) or a tracked algo
            # id (ExchangePositionRow) - never assume based on the other
            # leg or on global capability alone, in case the two legs ever
            # disagree (e.g. a mid-migration position).
            for order in await self.client.get_open_orders(symbol):
                if order.type == order_type:
                    await self.client.cancel_order(symbol, order.order_id)
                    modes.audit("protective_order_canceled", symbol=symbol,
                                detail={"kind": kind, "order_id": order.order_id, "provider": "classic"})
            existing_algo_id = _tracked_algo_id(self.mode, symbol, leg)
            if existing_algo_id:
                try:
                    await protection_provider.cancel_leg(self.client, symbol, existing_algo_id, protection_provider.ALGO)
                    modes.audit("protective_order_canceled", symbol=symbol,
                                detail={"kind": kind, "order_id": existing_algo_id, "provider": "algo"})
                except Exception:
                    pass  # already gone (triggered/expired) - not a reason to block the replacement

            new_order_id = None
            provider_used = None
            if price is not None:
                try:
                    hedge_mode = await self.client.get_position_mode()
                except Exception:
                    hedge_mode = False
                leg_result = await protection_provider.place_leg(
                    client=self.client, mode=self.mode, symbol=symbol, position_side=pos.side, kind=leg,
                    price=price, quantity=pos.quantity, hedge_mode=hedge_mode,
                )
                if not leg_result.ok:
                    return self._result(False, f"update_{kind}", reason=leg_result.error)
                new_order_id = leg_result.order_id
                provider_used = leg_result.provider

            modes.audit("tp_sl_updated", symbol=symbol,
                        detail={"kind": kind, "price": price, "order_id": new_order_id, "provider": provider_used})

            # Phase 30: verify the COMBINED status (both legs) fresh against
            # Binance right after replacing this one leg - the same
            # ground-truth check every other protection mutation runs -
            # before persisting, so a stale/wrong status can never be
            # written just because this leg's placement alone looked fine.
            try:
                replaced_algo_id = new_order_id if provider_used == "algo" else None
                tp_algo_id_for_check = replaced_algo_id if leg == "tp" else _tracked_algo_id(self.mode, symbol, "tp")
                sl_algo_id_for_check = replaced_algo_id if leg == "sl" else _tracked_algo_id(self.mode, symbol, "sl")
                verified = await protection.resolve_protection(
                    self.client, symbol, tp_algo_id=tp_algo_id_for_check, sl_algo_id=sl_algo_id_for_check,
                )
                verified_status = verified.status
            except Exception:
                verified_status = None

            _store_protective_order_id(self.mode, symbol, kind, new_order_id, provider=provider_used or "classic",
                                       protection_status=verified_status)
            return self._result(True, f"update_{kind}", order_id=new_order_id, price=price, protection_provider=provider_used,
                                protection_status=verified_status)
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

    async def cancel_order(
        self, symbol: str, order_id: int | None = None, client_order_id: str | None = None,
        algo_id: int | None = None, client_algo_id: str | None = None, provider: str | None = None,
        **kwargs,
    ) -> RouterResult:
        """Cancels a resting order by whichever identifier(s) it carries.
        Phase 26 Algo Order Provider / Debug 1.pdf section 1: an id that
        matches this position's tracked tp_algo_id/sl_algo_id is an Algo
        Order API id, not a classic order id - callers that only ever knew
        one generic id (the pre-Debug-1 Cancel SL/TP buttons) keep working
        unchanged via that inference. Callers that already know the
        provider (the Real Open Orders row Cancel button, which reads it
        straight off the normalized order row) route directly, and never
        guess between orderId/algoId or mix classic and algo params on the
        same request."""
        symbol = symbol.upper()
        try:
            is_algo = provider == "algo" or (
                provider is None and (algo_id is not None or client_algo_id is not None)
            )
            candidate_id = algo_id if algo_id is not None else order_id
            if not is_algo and provider is None and candidate_id is not None:
                if candidate_id in (_tracked_algo_id(self.mode, symbol, "tp"), _tracked_algo_id(self.mode, symbol, "sl")):
                    is_algo = True

            if is_algo:
                resolved_algo_id = algo_id if algo_id is not None else order_id
                await protection_provider.cancel_leg(
                    self.client, symbol, resolved_algo_id, protection_provider.ALGO, client_algo_id=client_algo_id,
                )
                audit_id = resolved_algo_id if resolved_algo_id is not None else client_algo_id
                modes.audit("order_canceled", symbol=symbol, detail={"order_id": audit_id, "provider": "algo"})
                _invalidate_snapshot_after_cancel()
                return self._result(
                    True, "cancel_order",
                    # algo_id stringified for the same JSON-number-precision
                    # reason as BinanceOrder.to_dict() - see that docstring.
                    order={
                        "algo_id": str(resolved_algo_id) if resolved_algo_id is not None else None,
                        "client_algo_id": client_algo_id, "status": "CANCELED",
                    },
                )

            order = await self.client.cancel_order(symbol, order_id=order_id, orig_client_order_id=client_order_id)
            modes.audit("order_canceled", symbol=symbol, detail={"order_id": order.order_id, "provider": "classic"})
            _invalidate_snapshot_after_cancel()
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
        if canceled:
            _invalidate_snapshot_after_cancel()
        return self._result(not errors, "cancel_all_orders",
                            reason="; ".join(errors) if errors else None, canceled_symbols=canceled)


def _invalidate_snapshot_after_cancel() -> None:
    """A confirmed cancel changes the account's open-orders (and possibly
    margin) state immediately. Without this, the very next read - including
    the frontend's own post-action refresh, which fires milliseconds after
    the cancel resolves - would still serve the pre-cancel snapshot for up
    to BINANCE_ORDERS_TTL_SECONDS, making a successful single-order cancel
    look like it silently did nothing (the row stays in the table) even
    though Binance already canceled it. Same cache the TP/SL mutation path
    already invalidates (see _bump_protection_revision)."""
    from app.exchanges.binance_snapshot_service import snapshot_service
    snapshot_service.invalidate_after_protection_mutation()
    protection.reset_algo_lookup_cache()


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


def _tracked_algo_id(mode: str, symbol: str, leg: str) -> int | None:
    """The tracked algo id (Phase 26 Algo Order Provider) for one leg
    ("tp" or "sl") of a position, if that leg is currently on the algo
    provider - the only way to find an algo order, since no reliable
    "list open algo orders" endpoint exists for this account (see
    app.exchanges.binance_algo_provider)."""
    db = SessionLocal()
    try:
        row = (
            db.query(ExchangePositionRow)
            .filter(ExchangePositionRow.mode == mode, ExchangePositionRow.symbol == symbol)
            .first()
        )
        if not row:
            return None
        return row.tp_algo_id if leg == "tp" else row.sl_algo_id
    finally:
        db.close()


def _bump_protection_revision(row: ExchangePositionRow, status: str | None) -> None:
    """Phase 30: called on EVERY confirmed protection write (fresh entry,
    edit, watchdog repair) - never on a mere read. Increments
    protection_revision so a stale cached read (or an in-flight resolver
    call started before this write landed) can be recognized as older than
    the just-confirmed state instead of silently winning a race. Also
    invalidates the shared Binance snapshot cache and the algo lookup
    cache immediately, so the very next risk-gate/portfolio/watchdog read
    - not just the next one after a TTL expires - sees this result."""
    if status is not None:
        row.protection_status = status
        row.protection_verified_at = datetime.now(timezone.utc)
    row.protection_revision = (row.protection_revision or 0) + 1

    from app.exchanges.binance_snapshot_service import snapshot_service
    from app.trading import protection
    snapshot_service.invalidate_after_protection_mutation()
    protection.reset_algo_lookup_cache()


def _store_protective_order_id(mode: str, symbol: str, kind: str, order_id: int | None, provider: str = "classic",
                               protection_status: str | None = None) -> None:
    """Single-leg update (used by _replace_protective_order): sets one
    classic or algo id, clearing the other surface's id for that leg so a
    stale id from a prior provider can never be read back as live.
    `protection_status` (Phase 30) is the freshly resolve_protection()-
    verified combined status covering BOTH legs, computed by the caller
    right after this leg was replaced - never guessed from this leg alone."""
    db = SessionLocal()
    try:
        row = (
            db.query(ExchangePositionRow)
            .filter(ExchangePositionRow.mode == mode, ExchangePositionRow.symbol == symbol)
            .first()
        )
        if row:
            is_algo = provider == "algo"
            if kind == "stop_loss":
                row.stop_loss_order_id = str(order_id) if (order_id and not is_algo) else None
                row.sl_algo_id = order_id if (order_id and is_algo) else None
            else:
                row.take_profit_order_id = str(order_id) if (order_id and not is_algo) else None
                row.tp_algo_id = order_id if (order_id and is_algo) else None
            row.protection_provider = provider
            row.updated_at = datetime.now(timezone.utc)
            _bump_protection_revision(row, protection_status)
            db.commit()
    finally:
        db.close()


def _store_protection_metadata(mode: str, symbol: str, provider: str, tp_order_id: int | None,
                               sl_order_id: int | None, tp_algo_id: int | None, sl_algo_id: int | None,
                               protection_status: str | None = None) -> None:
    """Full upsert after a fresh entry's protection placement (or a
    watchdog repair, or the manual /protect endpoint) - creates the row if
    sync_positions hasn't run yet, rather than silently no-op'ing (unlike
    _store_protective_order_id, which only ever edits an existing
    replace-flow row). `protection_status` (Phase 30) is the caller's
    already-verified resolve_protection() result - this is the ONE place
    every protection-mutation code path funnels through, so bumping
    protection_revision and invalidating caches here covers all of them."""
    db = SessionLocal()
    try:
        row = (
            db.query(ExchangePositionRow)
            .filter(ExchangePositionRow.mode == mode, ExchangePositionRow.symbol == symbol)
            .first()
        )
        if not row:
            return  # position no longer open (e.g. closed due to failed protection) - nothing to store
        row.protection_provider = provider
        row.stop_loss_order_id = str(sl_order_id) if sl_order_id else None
        row.take_profit_order_id = str(tp_order_id) if tp_order_id else None
        row.sl_algo_id = sl_algo_id
        row.tp_algo_id = tp_algo_id
        row.updated_at = datetime.now(timezone.utc)
        _bump_protection_revision(row, protection_status)
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

    def _cancel_provider(self):
        """Cancel/cancel-all are de-risking safety actions, not order
        placement - unlike every other method on this router, they must
        keep working no matter what the bot's active trading mode is
        (PAPER, TESTNET, LIVE, or LIVE_LOCKED), as long as a real Binance
        account is actually configured. TESTNET already resolves to a real
        (sandboxed) client via provider(). LIVE and LIVE_LOCKED share the
        one lazily-built live client - constructing it only requires the
        server env lock (BINANCE_LIVE_ENABLED), not the separate per-
        session user live-risk unlock, so an account that is merely locked
        (server enabled, user not yet unlocked) can still cancel its own
        resting orders. PAPER also reuses that live client rather than the
        no-op PaperExecutionProvider.cancel_order: the bot's active mode
        describes simulated trading, not whether a real order placed
        earlier (or manually on the exchange) still needs to come off the
        book - see Debug 1.pdf section 2."""
        mode = modes.effective_mode()
        if mode == modes.MODE_TESTNET:
            return self._testnet
        if self._live is None:
            self._live = BinanceExecutionProvider(testnet=False)
        return self._live

    async def cancel_order(self, **kwargs) -> RouterResult:
        try:
            provider = self._cancel_provider()
        except LiveTradingLocked as e:
            return RouterResult(ok=False, mode=modes.effective_mode(), action="cancel_order", reason=str(e))
        return await provider.cancel_order(**kwargs)

    async def cancel_all_orders(self, **kwargs) -> RouterResult:
        try:
            provider = self._cancel_provider()
        except LiveTradingLocked as e:
            return RouterResult(ok=False, mode=modes.effective_mode(), action="cancel_all_orders", reason=str(e))
        return await provider.cancel_all_orders(**kwargs)


router = ExecutionRouter()
