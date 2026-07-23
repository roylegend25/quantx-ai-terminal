"""catchup_progress() (the basis for GET /api/predictions/resolver/health's
total_due/total_overdue/healthy fields) must not count a prediction already
terminally classified VOID_DATA_GAP/VOID_INVALID_PREDICTION (lifecycle_status)
or permanent_data_gap (the legacy unresolved_status marker) as still overdue.

Regression: a large historical backlog of genuinely-permanent data gaps
(market data that will never exist for that resolution window) was being
reported as an alarming, growing "overdue" backlog and healthy=False,
when the resolver had actually already correctly triaged every one of
those rows to a terminal state and would never touch them again."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import outcome, resolver_status

PREFIX = "catchup-void-test-"


def _ledger(pid, lifecycle_status=None, unresolved_status=None, resolver_attempts=0, minutes_overdue=60):
    now = datetime.now(timezone.utc)
    return PredictionLedger(
        prediction_id=PREFIX + pid, candidate_id=f"cand-{PREFIX}{pid}", decision_id="d", user_id="admin",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name="trend", source_version="1",
        symbol="BTCUSDT", timeframe="4h", direction="LONG", confidence=0.7,
        target_horizon_seconds=86400, feature_snapshot_hash=f"h-{pid}", generated_at=now - timedelta(minutes=minutes_overdue + 5),
        resolution_deadline=now - timedelta(minutes=minutes_overdue), reference_price=100.0,
        lifecycle_status=lifecycle_status, unresolved_status=unresolved_status, resolver_attempts=resolver_attempts,
    )


@pytest.fixture
def db():
    session = SessionLocal()
    ids = [r[0] for r in session.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like(f"{PREFIX}%")).all()]
    if ids:
        session.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    yield session
    ids = [r[0] for r in session.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like(f"{PREFIX}%")).all()]
    if ids:
        session.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    session.close()


def test_void_data_gap_rows_are_excluded_from_total_due_and_overdue(db):
    before = resolver_status.catchup_progress(db)
    db.add(_ledger("void", lifecycle_status=outcome.VOID_DATA_GAP, unresolved_status="permanent_data_gap", resolver_attempts=23))
    db.commit()
    # The VOID row must not inflate total_due/total_overdue or per-symbol counts.
    after = resolver_status.catchup_progress(db)
    assert after["btc"]["due"] == before["btc"]["due"]
    assert after["btc"]["overdue"] == before["btc"]["overdue"]


def test_void_data_gap_row_still_counted_as_permanently_failed(db):
    db.add(_ledger("void2", lifecycle_status=outcome.VOID_DATA_GAP, unresolved_status="permanent_data_gap", resolver_attempts=23))
    db.commit()
    progress = resolver_status.catchup_progress(db)
    assert progress["permanently_failed_total"] >= 1


def test_genuinely_actionable_overdue_row_is_still_counted(db):
    db.add(_ledger("actionable", lifecycle_status=None, unresolved_status=None, resolver_attempts=1))
    db.commit()
    progress = resolver_status.catchup_progress(db)
    assert progress["btc"]["due"] >= 1
    assert progress["btc"]["overdue"] >= 1


def test_void_row_does_not_count_toward_oldest_overdue_age(db):
    """A VOID row with an ancient deadline must not make oldest_overdue_age_seconds
    look artificially huge once nothing genuinely actionable is that old."""
    db.add(_ledger("ancient-void", lifecycle_status=outcome.VOID_DATA_GAP, unresolved_status="permanent_data_gap",
                   resolver_attempts=23, minutes_overdue=999999))
    db.add(_ledger("recent-actionable", lifecycle_status=None, unresolved_status=None, resolver_attempts=1, minutes_overdue=5))
    db.commit()
    progress = resolver_status.catchup_progress(db)
    # oldest_overdue_age_seconds is global (not symbol-scoped) - just assert
    # it isn't pinned to the multi-year-old VOID row's deadline.
    if progress["oldest_overdue_age_seconds"] is not None:
        assert progress["oldest_overdue_age_seconds"] < 999999 * 60
