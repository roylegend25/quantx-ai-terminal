"""Authoritative, idempotent P&L reconciliation for one closed real trade.

Binance's /fapi/v1/income rows return orderId = null on this account, so
close_position()'s original matching (income row orderId == our order id)
always found zero rows and silently left LiveVerificationRun's
completed_trades_during_this_run / realised_loss_during_this_run at zero.

This module reconciles from the sources that are actually populated:
  - /fapi/v1/userTrades (per-fill records) for realized_pnl and commission,
    matched by the exact, globally-unique Binance order id - never by time
    alone, so an unrelated trade on the same symbol/day can never be pulled
    in.
  - /fapi/v1/income (incomeType=FUNDING_FEE) for funding, matched by symbol
    and the fill time window - funding rows are never associated with an
    order id by Binance itself, so a time window bounded by the trade's own
    fill timestamps is the correct match key, not a bug workaround.

Idempotent AND self-healing: entry_order_id is unique on
BinanceTradeReconciliation, so the P&L row itself is only ever written once.
Separately, run_counters_applied_at tracks whether the verification-run
counters have actually been updated from that row - if an earlier call
persisted the P&L but then failed before reaching the counter step (exactly
what happened before this fix existed), a later call completes that step
instead of silently no-op'ing forever.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import BinanceTradeReconciliation


class ReconciliationError(RuntimeError):
    pass


@dataclass
class ReconciliationResult:
    row: BinanceTradeReconciliation
    already_reconciled: bool


def _apply_run_counters(db: Session, row: BinanceTradeReconciliation) -> None:
    """Idempotent: only actually applies once per row (guarded by
    run_counters_applied_at), regardless of how many times it's called."""
    if row.run_counters_applied_at is not None or not row.verification_run_id:
        return
    from app.db.models import LiveVerificationRun
    from app.trading import verification_runs

    run = db.get(LiveVerificationRun, row.verification_run_id)
    if run and run.status == "stopped":
        # The run was already stopped before this reconciliation ran - e.g.
        # a caller stopped it separately (single-trade cap) or an earlier
        # reconciliation attempt persisted the P&L row but failed before
        # reaching this step. Backfill the counters without touching
        # status/live_execution_enabled; never usable to affect a still-
        # active run (see record_closed_trade_retroactive).
        verification_runs.record_closed_trade_retroactive(
            db, row.verification_run_id, net_realised_pnl=row.net_realised_pnl,
        )
    else:
        verification_runs.record_closed_trade(
            db, row.verification_run_id, net_realised_pnl=row.net_realised_pnl,
        )
    row.run_counters_applied_at = datetime.now(timezone.utc)
    db.commit()


async def reconcile_closed_trade(
    client,
    db: Session,
    *,
    symbol: str,
    entry_order_id: int | str,
    exit_order_id: int | str,
    verification_run_id: str | None = None,
    funding_window_buffer_ms: int = 2_000,
) -> ReconciliationResult:
    """Reconcile one closed trade's gross/commission/funding/net P&L.

    Safe to call more than once for the same entry_order_id: the P&L row is
    written once (unique on entry_order_id) and the verification-run
    counters are applied at most once (guarded separately, see
    _apply_run_counters) - a repeat call never re-touches either.
    """
    symbol = symbol.upper()
    entry_order_id = str(entry_order_id)
    exit_order_id = str(exit_order_id)

    existing = (
        db.query(BinanceTradeReconciliation)
        .filter_by(entry_order_id=entry_order_id)
        .first()
    )
    if existing:
        _apply_run_counters(db, existing)
        return ReconciliationResult(row=existing, already_reconciled=True)

    fills = await client.get_trade_history(symbol, limit=1000)
    entry_fills = [f for f in fills if str(f.order_id) == entry_order_id]
    exit_fills = [f for f in fills if str(f.order_id) == exit_order_id]

    if not entry_fills:
        raise ReconciliationError(
            f"No Binance fills found for entry order {entry_order_id} on {symbol} - "
            "refusing to record a P&L of zero for an unverified trade."
        )
    if not exit_fills:
        raise ReconciliationError(
            f"No Binance fills found for exit order {exit_order_id} on {symbol} - "
            "refusing to record a P&L of zero for an unverified trade."
        )

    matched_fills = entry_fills + exit_fills
    gross_pnl = sum(f.realized_pnl for f in matched_fills)
    total_commission = sum(f.commission for f in matched_fills)

    fill_times = [f.time for f in matched_fills if f.time is not None]
    window_start = min(fill_times) - funding_window_buffer_ms if fill_times else None
    window_end = max(fill_times) + funding_window_buffer_ms if fill_times else None

    total_funding = 0.0
    if window_start is not None and window_end is not None:
        funding_rows = await client.get_income_history(limit=1000, income_type="FUNDING_FEE")
        total_funding = sum(
            float(r.get("income") or 0.0)
            for r in funding_rows
            if r.get("symbol") == symbol and window_start <= (r.get("time") or 0) <= window_end
        )

    net_realised_pnl = gross_pnl - total_commission + total_funding

    row = BinanceTradeReconciliation(
        entry_order_id=entry_order_id,
        exit_order_id=exit_order_id,
        verification_run_id=verification_run_id,
        symbol=symbol,
        entry_fill_count=len(entry_fills),
        exit_fill_count=len(exit_fills),
        gross_pnl=gross_pnl,
        total_commission=total_commission,
        total_funding=total_funding,
        net_realised_pnl=net_realised_pnl,
        window_start_ms=window_start,
        window_end_ms=window_end,
        reconciliation_source="user_trades+income_funding",
        audit_note=(
            f"Reconciled from {len(entry_fills)} entry fill(s) and {len(exit_fills)} exit fill(s) "
            f"on /fapi/v1/userTrades, matched by exact Binance order id (entry={entry_order_id}, "
            f"exit={exit_order_id}). Funding matched by symbol={symbol} within fill time window "
            f"[{window_start},{window_end}] via /fapi/v1/income (incomeType=FUNDING_FEE). "
            "Income-history orderId is null on this account and was not used to match trade P&L "
            "or commission - only userTrades fills were used for those. "
            f"gross_pnl={gross_pnl!r} total_commission={total_commission!r} total_funding={total_funding!r} "
            f"net_realised_pnl={net_realised_pnl!r}. Reconciled at "
            f"{datetime.now(timezone.utc).isoformat()}."
        ),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(BinanceTradeReconciliation)
            .filter_by(entry_order_id=entry_order_id)
            .first()
        )
        _apply_run_counters(db, existing)
        return ReconciliationResult(row=existing, already_reconciled=True)
    db.refresh(row)

    _apply_run_counters(db, row)

    return ReconciliationResult(row=row, already_reconciled=False)
