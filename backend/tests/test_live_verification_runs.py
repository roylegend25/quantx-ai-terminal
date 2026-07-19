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
    for expected in range(1, 7):
        assert verification_runs.claim_attempt(db) == run.verification_run_id
        assert db.get(LiveVerificationRun, run.verification_run_id).attempts_during_this_run == expected
    with pytest.raises(verification_runs.VerificationBlocked):
        verification_runs.claim_attempt(db)
    stopped = db.get(LiveVerificationRun, run.verification_run_id)
    assert stopped.status == "stopped"
    assert stopped.stop_reason == "maximum_six_attempts_reached"


def test_second_profitable_closed_trade_stops_run_and_execution(db):
    control = db.get(TradingControl, 1) or TradingControl(id=1, mode="PAPER")
    control.execution_enabled = True
    control.execution_state = "running"
    db.add(control)
    db.commit()
    run = verification_runs.prepare_run(db, starting_balance=1000)
    verification_runs.record_closed_trade(db, run.verification_run_id, net_realised_pnl=1.0)
    final = verification_runs.record_closed_trade(db, run.verification_run_id, net_realised_pnl=0.01)
    assert final.successful_trades_during_this_run == 2
    assert final.status == "stopped"
    assert final.live_execution_enabled is False
    assert db.get(TradingControl, 1).execution_state == "stopped"


def test_loss_limit_is_point_three_percent_of_starting_balance(db):
    run = verification_runs.prepare_run(db, starting_balance=1000)
    final = verification_runs.record_closed_trade(db, run.verification_run_id, net_realised_pnl=-3.0)
    assert final.realised_loss_during_this_run == 3.0
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
