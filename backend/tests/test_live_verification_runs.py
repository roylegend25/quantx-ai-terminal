from datetime import datetime, timezone

import pytest

from app.db.models import BinanceExecutionAttempt, LiveVerificationRun, TradingControl
from app.db.session import SessionLocal
from app.trading import verification_runs
from app.trading.execution_pipeline import PipelineRecorder


def _attempt(mode="BINANCE_LIVE", run_id=None):
    return BinanceExecutionAttempt(
        verification_run_id=run_id, mode=mode, symbol="BTCUSDT", side="LONG", is_test=False,
        stages=[], final_status="failed", created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def db():
    session = SessionLocal()
    session.query(BinanceExecutionAttempt).filter(BinanceExecutionAttempt.symbol == "VERIFYTEST").delete()
    session.query(LiveVerificationRun).delete()
    session.commit()
    yield session
    session.query(BinanceExecutionAttempt).filter(BinanceExecutionAttempt.verification_run_id.isnot(None)).delete()
    session.query(LiveVerificationRun).delete()
    session.commit()
    session.close()


def test_new_run_snapshots_history_but_starts_at_zero(db):
    lifetime_before = db.query(BinanceExecutionAttempt).filter(
        BinanceExecutionAttempt.mode == "BINANCE_LIVE", BinanceExecutionAttempt.is_test.is_(False)
    ).count()
    run = verification_runs.prepare_run(db, starting_balance=1000)
    assert run.baseline_historical_attempt_count == lifetime_before
    assert run.attempts_during_this_run == 0
    assert run.live_execution_enabled is False
    assert db.query(BinanceExecutionAttempt).filter(BinanceExecutionAttempt.verification_run_id == run.verification_run_id).count() == 0


def test_attempt_limit_is_run_scoped_and_atomic(db):
    run = verification_runs.prepare_run(db, starting_balance=1000)
    verification_runs.set_live_execution(db, run.verification_run_id, True)
    for expected in range(1, 5):
        assert verification_runs.validate_attempt(db) == run.verification_run_id
        assert verification_runs.claim_attempt(db, run.verification_run_id) == run.verification_run_id
        assert db.get(LiveVerificationRun, run.verification_run_id).attempts_during_this_run == expected
    with pytest.raises(verification_runs.VerificationBlocked):
        verification_runs.validate_attempt(db)
    stopped = db.get(LiveVerificationRun, run.verification_run_id)
    assert stopped.status == "stopped"
    assert stopped.stop_reason == "maximum_four_attempts_reached"


def test_unacknowledged_submission_does_not_consume_attempt(db):
    run = verification_runs.prepare_run(db, starting_balance=1000)
    verification_runs.set_live_execution(db, run.verification_run_id, True)
    verification_runs.validate_attempt(db)
    verification_runs.claim_attempt(db, run.verification_run_id)
    verification_runs.release_unacknowledged_attempt(db, run.verification_run_id)
    db.expire_all()
    assert db.get(LiveVerificationRun, run.verification_run_id).attempts_during_this_run == 0


def test_second_closed_trade_stops_run_and_execution_even_when_not_profitable(db):
    control = db.get(TradingControl, 1) or TradingControl(id=1, mode="PAPER")
    control.execution_enabled = True
    control.execution_state = "running"
    db.add(control)
    db.commit()
    run = verification_runs.prepare_run(db, starting_balance=1000)
    verification_runs.record_closed_trade(db, run.verification_run_id, net_realised_pnl=1.0)
    final = verification_runs.record_closed_trade(db, run.verification_run_id, net_realised_pnl=-0.01)
    assert final.successful_trades_during_this_run == 1
    assert final.completed_trades_during_this_run == 2
    assert final.status == "stopped"
    assert final.stop_reason == "TWO_REAL_TRADE_LIFECYCLES_VERIFIED"
    assert final.live_execution_enabled is False
    assert db.get(TradingControl, 1).execution_state == "stopped"


def test_combined_realised_loss_limit_is_fifty_cents(db):
    run = verification_runs.prepare_run(db, starting_balance=1000)
    final = verification_runs.record_closed_trade(db, run.verification_run_id, net_realised_pnl=-0.50)
    assert final.realised_loss_during_this_run == 0.50
    assert final.status == "stopped"
    assert final.stop_reason == "maximum_realised_loss_reached"


def test_pipeline_row_carries_run_id_without_touching_history(db):
    run = verification_runs.prepare_run(db, starting_balance=1000)
    before = db.query(BinanceExecutionAttempt).filter(BinanceExecutionAttempt.verification_run_id.is_(None)).count()
    recorder = PipelineRecorder(mode="BINANCE_LIVE", symbol="VERIFYTEST", side="LONG",
                                verification_run_id=run.verification_run_id)
    recorder.finish("failed", "test")
    db.expire_all()
    tagged = db.query(BinanceExecutionAttempt).filter_by(verification_run_id=run.verification_run_id).one()
    assert tagged.symbol == "VERIFYTEST"
    assert db.query(BinanceExecutionAttempt).filter(BinanceExecutionAttempt.verification_run_id.is_(None)).count() == before


def test_previous_stopped_run_is_immutable_and_new_run_gets_distinct_id(db):
    previous = verification_runs.prepare_run(db, starting_balance=24.86306379)
    previous_id = previous.verification_run_id
    verification_runs.stop_run(db, previous_id, "binance_reconciliation_timestamp_sync_failed")
    before = {
        "status": previous.status,
        "stop_reason": previous.stop_reason,
        "attempts": previous.attempts_during_this_run,
        "successes": previous.successful_trades_during_this_run,
        "ended_at": previous.ended_at,
    }

    new = verification_runs.prepare_run(db, starting_balance=24.86306379)
    db.expire_all()
    unchanged = db.get(LiveVerificationRun, previous_id)
    assert new.verification_run_id != previous_id
    assert {
        "status": unchanged.status,
        "stop_reason": unchanged.stop_reason,
        "attempts": unchanged.attempts_during_this_run,
        "successes": unchanged.successful_trades_during_this_run,
        "ended_at": unchanged.ended_at,
    } == before
