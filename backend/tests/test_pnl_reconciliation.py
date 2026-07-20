import asyncio
from datetime import datetime, timezone

import pytest

from app.db.models import BinanceTradeReconciliation, LiveVerificationRun
from app.db.session import SessionLocal
from app.exchanges.binance_models import BinanceUserTrade
from app.trading.pnl_reconciliation import ReconciliationError, reconcile_closed_trade

SYMBOL = "ETHUSDT"
ENTRY_ORDER_ID = 111111
EXIT_ORDER_ID = 222222
UNRELATED_ORDER_ID = 999999
RUN_ID = "pnltest-run"


def _trade(order_id, trade_id, side, price, qty, realized_pnl, commission, time_ms):
    return BinanceUserTrade(
        trade_id=trade_id, order_id=order_id, symbol=SYMBOL, side=side, price=price,
        quantity=qty, realized_pnl=realized_pnl, commission=commission,
        commission_asset="USDT", time=time_ms,
    )


def _income(symbol, income_type, income, time_ms, order_id=None):
    return {
        "symbol": symbol, "income_type": income_type, "income": income, "asset": "USDT",
        "info": None, "time": time_ms, "orderId": order_id, "tranId": None,
    }


class FakeClient:
    """Mimics BinanceFuturesClient's read-only trade/income surface."""

    def __init__(self, trades=None, income=None):
        self.trades = trades or []
        self.income = income or []
        self.trade_history_calls = 0
        self.income_history_calls = 0

    async def get_trade_history(self, symbol, limit=1000, start_time=None, end_time=None):
        self.trade_history_calls += 1
        return [t for t in self.trades if t.symbol == symbol]

    async def get_income_history(self, limit=1000, income_type=None):
        self.income_history_calls += 1
        rows = self.income
        if income_type:
            rows = [r for r in rows if r.get("income_type") == income_type]
        return rows


@pytest.fixture
def db():
    session = SessionLocal()
    session.query(BinanceTradeReconciliation).filter(
        BinanceTradeReconciliation.entry_order_id == str(ENTRY_ORDER_ID)
    ).delete(synchronize_session=False)
    session.query(LiveVerificationRun).filter(LiveVerificationRun.verification_run_id == RUN_ID).delete()
    session.commit()
    yield session
    session.query(BinanceTradeReconciliation).filter(
        BinanceTradeReconciliation.entry_order_id == str(ENTRY_ORDER_ID)
    ).delete(synchronize_session=False)
    session.query(LiveVerificationRun).filter(LiveVerificationRun.verification_run_id == RUN_ID).delete()
    session.commit()
    session.close()


def _make_run(db):
    run = LiveVerificationRun(
        verification_run_id=RUN_ID, started_at=datetime.now(timezone.utc),
        starting_balance=100.0, baseline_historical_attempt_count=0, status="running",
        live_execution_enabled=True,
    )
    db.add(run)
    db.commit()
    return run


def _reconcile(client, db, entry_order_id=ENTRY_ORDER_ID, exit_order_id=EXIT_ORDER_ID, run_id=RUN_ID):
    return asyncio.run(reconcile_closed_trade(
        client, db, symbol=SYMBOL, entry_order_id=entry_order_id, exit_order_id=exit_order_id,
        verification_run_id=run_id,
    ))


def test_income_row_with_null_order_id_does_not_break_reconciliation(db):
    """Binance income rows on this account always have orderId=null - trade
    P&L/commission must come from userTrades regardless, and reconciliation
    must succeed rather than silently producing a zero result."""
    _make_run(db)
    client = FakeClient(
        trades=[
            _trade(ENTRY_ORDER_ID, 1, "BUY", 1000.0, 1.0, 0.0, 0.5, 1_000_000),
            _trade(EXIT_ORDER_ID, 2, "SELL", 1010.0, 1.0, 10.0, 0.5, 1_000_100),
        ],
        income=[
            _income(SYMBOL, "COMMISSION", -0.5, 1_000_000, order_id=None),
            _income(SYMBOL, "REALIZED_PNL", 10.0, 1_000_100, order_id=None),
        ],
    )
    result = _reconcile(client, db)
    assert result.already_reconciled is False
    assert result.row.gross_pnl == 10.0
    assert result.row.total_commission == 1.0
    assert result.row.net_realised_pnl == 9.0


def test_entry_and_exit_with_multiple_fills_are_all_summed(db):
    _make_run(db)
    client = FakeClient(trades=[
        _trade(ENTRY_ORDER_ID, 1, "BUY", 999.0, 0.6, 0.0, 0.3, 1_000_000),
        _trade(ENTRY_ORDER_ID, 2, "BUY", 1001.0, 0.4, 0.0, 0.2, 1_000_001),
        _trade(EXIT_ORDER_ID, 3, "SELL", 1010.0, 0.7, 6.0, 0.35, 1_000_100),
        _trade(EXIT_ORDER_ID, 4, "SELL", 1011.0, 0.3, 3.0, 0.15, 1_000_101),
    ])
    result = _reconcile(client, db)
    assert result.row.entry_fill_count == 2
    assert result.row.exit_fill_count == 2
    assert result.row.gross_pnl == 9.0
    assert result.row.total_commission == 1.0


def test_commissions_in_separate_rows_are_all_included(db):
    """Each fill carries its own commission - a market order split into many
    small fills must not lose any of them."""
    _make_run(db)
    entry_fills = [_trade(ENTRY_ORDER_ID, i, "BUY", 1000.0, 0.2, 0.0, 0.1, 1_000_000 + i) for i in range(5)]
    exit_fills = [_trade(EXIT_ORDER_ID, 100 + i, "SELL", 1005.0, 0.2, 1.0, 0.1, 1_000_200 + i) for i in range(5)]
    client = FakeClient(trades=entry_fills + exit_fills)
    result = _reconcile(client, db)
    assert result.row.total_commission == pytest.approx(1.0)
    assert result.row.gross_pnl == pytest.approx(5.0)


def test_funding_within_window_is_included_and_outside_window_excluded(db):
    _make_run(db)
    client = FakeClient(
        trades=[
            _trade(ENTRY_ORDER_ID, 1, "BUY", 1000.0, 1.0, 0.0, 0.5, 1_000_000),
            _trade(EXIT_ORDER_ID, 2, "SELL", 1010.0, 1.0, 10.0, 0.5, 1_000_100),
        ],
        income=[
            _income(SYMBOL, "FUNDING_FEE", 0.25, 1_000_050),  # inside window
            _income(SYMBOL, "FUNDING_FEE", 99.0, 1_500_000),  # far outside window - must be excluded
            _income("BTCUSDT", "FUNDING_FEE", 50.0, 1_000_050),  # different symbol - must be excluded
        ],
    )
    result = _reconcile(client, db)
    assert result.row.total_funding == 0.25
    assert result.row.net_realised_pnl == pytest.approx(10.0 - 1.0 + 0.25)


def test_repeated_reconciliation_is_idempotent(db):
    _make_run(db)
    client = FakeClient(trades=[
        _trade(ENTRY_ORDER_ID, 1, "BUY", 1000.0, 1.0, 0.0, 0.5, 1_000_000),
        _trade(EXIT_ORDER_ID, 2, "SELL", 1010.0, 1.0, 10.0, 0.5, 1_000_100),
    ])
    first = _reconcile(client, db)
    second = _reconcile(client, db)
    assert first.already_reconciled is False
    assert second.already_reconciled is True
    assert first.row.id == second.row.id
    assert db.query(BinanceTradeReconciliation).filter_by(entry_order_id=str(ENTRY_ORDER_ID)).count() == 1


def test_retry_completes_counter_update_that_a_prior_call_left_unfinished(db):
    """Reproduces the real incident: an earlier reconciliation persisted the
    P&L row but crashed before applying it to the run counters (e.g. the run
    was stopped by something else in between). A retry must complete that
    step exactly once, not skip it forever just because the row exists."""
    run = _make_run(db)
    row = BinanceTradeReconciliation(
        entry_order_id=str(ENTRY_ORDER_ID), exit_order_id=str(EXIT_ORDER_ID),
        verification_run_id=RUN_ID, symbol=SYMBOL,
        entry_fill_count=1, exit_fill_count=1, gross_pnl=10.0, total_commission=1.0,
        total_funding=0.0, net_realised_pnl=9.0, run_counters_applied_at=None,
    )
    db.add(row)
    db.commit()

    client = FakeClient(trades=[
        _trade(ENTRY_ORDER_ID, 1, "BUY", 1000.0, 1.0, 0.0, 0.5, 1_000_000),
        _trade(EXIT_ORDER_ID, 2, "SELL", 1010.0, 1.0, 10.0, 0.5, 1_000_100),
    ])
    result = _reconcile(client, db)
    assert result.already_reconciled is True
    assert client.trade_history_calls == 0  # no need to re-fetch Binance data - the row already exists

    updated_run = db.get(LiveVerificationRun, RUN_ID)
    assert updated_run.completed_trades_during_this_run == 1
    assert updated_run.successful_trades_during_this_run == 1

    # A further retry must not double-apply.
    second = _reconcile(client, db)
    assert second.already_reconciled is True
    final_run = db.get(LiveVerificationRun, RUN_ID)
    assert final_run.completed_trades_during_this_run == 1


def test_verification_run_counters_update_exactly_once(db):
    _make_run(db)
    client = FakeClient(trades=[
        _trade(ENTRY_ORDER_ID, 1, "BUY", 1000.0, 1.0, 0.0, 0.5, 1_000_000),
        _trade(EXIT_ORDER_ID, 2, "SELL", 1010.0, 1.0, 10.0, 0.5, 1_000_100),
    ])
    _reconcile(client, db)
    _reconcile(client, db)
    row = db.get(LiveVerificationRun, RUN_ID)
    assert row.completed_trades_during_this_run == 1
    assert row.successful_trades_during_this_run == 1
    assert row.realised_loss_during_this_run == 0.0


def test_unrelated_trade_same_symbol_same_day_is_never_associated(db):
    _make_run(db)
    client = FakeClient(trades=[
        _trade(ENTRY_ORDER_ID, 1, "BUY", 1000.0, 1.0, 0.0, 0.5, 1_000_000),
        _trade(EXIT_ORDER_ID, 2, "SELL", 1010.0, 1.0, 10.0, 0.5, 1_000_100),
        # An unrelated trade on the same symbol, same day, overlapping window.
        _trade(UNRELATED_ORDER_ID, 3, "BUY", 995.0, 5.0, 500.0, 25.0, 1_000_050),
    ])
    result = _reconcile(client, db)
    assert result.row.gross_pnl == 10.0
    assert result.row.total_commission == 1.0
    assert result.row.entry_fill_count == 1
    assert result.row.exit_fill_count == 1


def test_missing_entry_fill_raises_instead_of_recording_zero(db):
    _make_run(db)
    client = FakeClient(trades=[
        _trade(EXIT_ORDER_ID, 2, "SELL", 1010.0, 1.0, 10.0, 0.5, 1_000_100),
    ])
    with pytest.raises(ReconciliationError):
        _reconcile(client, db)
    assert db.query(BinanceTradeReconciliation).filter_by(entry_order_id=str(ENTRY_ORDER_ID)).count() == 0


def test_missing_exit_fill_raises_instead_of_recording_zero(db):
    _make_run(db)
    client = FakeClient(trades=[
        _trade(ENTRY_ORDER_ID, 1, "BUY", 1000.0, 1.0, 0.0, 0.5, 1_000_000),
    ])
    with pytest.raises(ReconciliationError):
        _reconcile(client, db)


def test_reconciling_an_already_stopped_run_backfills_counters_without_reactivating_it(db):
    """Mirrors the real incident: close_position()'s reconciliation silently
    failed under the old orderId bug, a caller stopped the run anyway, and
    this fix must be able to backfill the true P&L afterward without ever
    resuming trading on that run."""
    run = _make_run(db)
    run.status = "stopped"
    run.stop_reason = "ONE_REAL_EXECUTION_TEST_COMPLETED"
    run.live_execution_enabled = False
    db.commit()

    client = FakeClient(trades=[
        _trade(ENTRY_ORDER_ID, 1, "BUY", 1000.0, 1.0, 0.0, 0.5, 1_000_000),
        _trade(EXIT_ORDER_ID, 2, "SELL", 1010.0, 1.0, 10.0, 0.5, 1_000_100),
    ])
    result = _reconcile(client, db)
    assert result.row.net_realised_pnl == pytest.approx(9.0)

    row = db.get(LiveVerificationRun, RUN_ID)
    assert row.completed_trades_during_this_run == 1
    assert row.successful_trades_during_this_run == 1
    assert row.status == "stopped"
    assert row.stop_reason == "ONE_REAL_EXECUTION_TEST_COMPLETED"
    assert row.live_execution_enabled is False


def test_retroactive_backfill_refuses_to_touch_an_active_run():
    from app.trading.verification_runs import VerificationBlocked, record_closed_trade_retroactive

    db = SessionLocal()
    db.query(LiveVerificationRun).filter(LiveVerificationRun.verification_run_id == RUN_ID).delete()
    db.commit()
    try:
        run = LiveVerificationRun(
            verification_run_id=RUN_ID, started_at=datetime.now(timezone.utc),
            starting_balance=100.0, baseline_historical_attempt_count=0, status="running",
            live_execution_enabled=True,
        )
        db.add(run)
        db.commit()
        with pytest.raises(VerificationBlocked):
            record_closed_trade_retroactive(db, RUN_ID, net_realised_pnl=5.0)
        row = db.get(LiveVerificationRun, RUN_ID)
        assert row.completed_trades_during_this_run == 0
    finally:
        db.query(LiveVerificationRun).filter(LiveVerificationRun.verification_run_id == RUN_ID).delete()
        db.commit()
        db.close()
