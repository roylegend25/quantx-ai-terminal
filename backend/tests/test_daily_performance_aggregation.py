"""Daily performance snapshot aggregation (main-purpose consolidation,
Stage 2). Verifies the mandatory properties from the task spec: idempotent
re-runs, LONG/SHORT accuracy symmetry, neutral kept separate from
correct/wrong, and unresolved predictions excluded from accuracy."""
import uuid
from datetime import datetime, timedelta, timezone

from app.analytics.daily_performance import run_daily_aggregation
from app.db.models import ActiveDriveDecision, PredictionLedger, PredictionResolution
from app.db.session import SessionLocal

TODAY = datetime.now(timezone.utc).date()
DAY_START = datetime(TODAY.year, TODAY.month, TODAY.day, 12, 0, tzinfo=timezone.utc)


def _make_decision(db, decision_id, symbol="BTCUSDT", timeframe="15m", signal="LONG", execution_approved=True):
    decision = ActiveDriveDecision(
        decision_id=decision_id, user_id="agg-test-user", engine="active_drive_v2", engine_version="2.2.0",
        symbol=symbol, timeframe=timeframe, signal=signal, confidence=70.0,
        eligible_for_execution=execution_approved, execution_approved=execution_approved,
        created_at=DAY_START,
    )
    db.add(decision)
    return decision


def _make_prediction(
    db, *, decision_id, source_type="strategy", source_name="trend", source_version="2.1.0",
    symbol="BTCUSDT", timeframe="15m", direction="LONG", market_regime="trending_up",
    lifecycle_status="RESOLVED_CORRECT", actual_return=0.01, generated_at=None,
):
    prediction_id = uuid.uuid4().hex
    db.add(PredictionLedger(
        prediction_id=prediction_id, decision_id=decision_id, candidate_id=uuid.uuid4().hex,
        user_id="agg-test-user", engine="active_drive_v2", engine_version="2.2.0",
        source_type=source_type, source_name=source_name, source_version=source_version,
        symbol=symbol, timeframe=timeframe, market_regime=market_regime, direction=direction,
        confidence=70.0, points=1.0, data_revision="test", feature_snapshot_hash=uuid.uuid4().hex,
        target_horizon_seconds=900, resolution_deadline=(generated_at or DAY_START) + timedelta(seconds=900),
        generated_at=generated_at or DAY_START, lifecycle_status=lifecycle_status,
    ))
    if lifecycle_status in ("RESOLVED_CORRECT", "RESOLVED_WRONG", "RESOLVED_NEUTRAL"):
        correct = {"RESOLVED_CORRECT": True, "RESOLVED_WRONG": False, "RESOLVED_NEUTRAL": None}[lifecycle_status]
        db.add(PredictionResolution(
            prediction_id=prediction_id, actual_return=actual_return, resolved_direction=direction,
            correct=correct, neutral_result=lifecycle_status == "RESOLVED_NEUTRAL",
            resolution_reason="test", resolved_at=(generated_at or DAY_START) + timedelta(seconds=1000),
        ))
    return prediction_id


def _cleanup(db):
    db.query(PredictionResolution).delete()
    db.query(PredictionLedger).delete()
    db.query(ActiveDriveDecision).delete()
    db.commit()


def test_correct_wrong_neutral_and_unresolved_are_counted_separately():
    db = SessionLocal()
    try:
        _cleanup(db)
        decision_id = uuid.uuid4().hex
        _make_decision(db, decision_id)
        _make_prediction(db, decision_id=decision_id, lifecycle_status="RESOLVED_CORRECT")
        _make_prediction(db, decision_id=decision_id, lifecycle_status="RESOLVED_CORRECT")
        _make_prediction(db, decision_id=decision_id, lifecycle_status="RESOLVED_WRONG")
        _make_prediction(db, decision_id=decision_id, lifecycle_status="RESOLVED_NEUTRAL")
        _make_prediction(db, decision_id=decision_id, lifecycle_status="PENDING")
        db.commit()

        result = run_daily_aggregation(TODAY, db=db)
        assert result["scopes_written"] > 0

        from app.db.models import DailyStrategyPerformance
        row = db.query(DailyStrategyPerformance).filter(
            DailyStrategyPerformance.date == TODAY, DailyStrategyPerformance.contributor_name == "trend",
        ).first()
        assert row is not None
        assert row.total_eligible_predictions == 5
        assert row.resolved_predictions == 4
        assert row.correct == 2
        assert row.wrong == 1
        assert row.neutral == 1
        assert row.unresolved_not_due == 1
        # directional accuracy excludes neutral: correct / (correct + wrong)
        assert row.directional_accuracy == round(2 / 3, 4)
        assert row.neutral_rate == round(1 / 4, 4)
    finally:
        _cleanup(db)
        db.close()


def test_long_and_short_accuracy_are_symmetric():
    """A LONG prediction correct because price rose, and a SHORT prediction
    correct because price fell, must both count as directionally correct -
    accuracy must not be computed as if only one direction can "win"."""
    db = SessionLocal()
    try:
        _cleanup(db)
        long_decision = uuid.uuid4().hex
        short_decision = uuid.uuid4().hex
        _make_decision(db, long_decision, signal="LONG")
        _make_decision(db, short_decision, signal="SHORT")
        # LONG, price went up +1%: correct
        _make_prediction(db, decision_id=long_decision, direction="LONG",
                          lifecycle_status="RESOLVED_CORRECT", actual_return=0.01)
        # SHORT, price went down -1% (a win for a short): correct
        _make_prediction(db, decision_id=short_decision, direction="SHORT",
                          lifecycle_status="RESOLVED_CORRECT", actual_return=-0.01)
        db.commit()

        run_daily_aggregation(TODAY, db=db)

        from app.db.models import DailyStrategyPerformance
        long_row = db.query(DailyStrategyPerformance).filter(
            DailyStrategyPerformance.date == TODAY, DailyStrategyPerformance.direction == "LONG",
        ).first()
        short_row = db.query(DailyStrategyPerformance).filter(
            DailyStrategyPerformance.date == TODAY, DailyStrategyPerformance.direction == "SHORT",
        ).first()
        assert long_row.correct == 1 and long_row.directional_accuracy == 1.0
        assert short_row.correct == 1 and short_row.directional_accuracy == 1.0
        # Both signed (direction-relative) returns are positive - a "win" is
        # a win regardless of which direction it was.
        assert long_row.average_actual_return > 0
        assert short_row.average_actual_return > 0
    finally:
        _cleanup(db)
        db.close()


def test_aggregation_is_idempotent_on_rerun():
    db = SessionLocal()
    try:
        _cleanup(db)
        decision_id = uuid.uuid4().hex
        _make_decision(db, decision_id)
        _make_prediction(db, decision_id=decision_id, lifecycle_status="RESOLVED_CORRECT")
        _make_prediction(db, decision_id=decision_id, lifecycle_status="RESOLVED_WRONG")
        db.commit()

        run_daily_aggregation(TODAY, db=db)
        from app.db.models import DailyStrategyPerformance
        first_count = db.query(DailyStrategyPerformance).count()
        first_row = db.query(DailyStrategyPerformance).filter(
            DailyStrategyPerformance.date == TODAY, DailyStrategyPerformance.contributor_name == "trend",
        ).first()
        first_id = first_row.id

        # Re-running for the exact same date must upsert, not duplicate.
        run_daily_aggregation(TODAY, db=db)
        second_count = db.query(DailyStrategyPerformance).count()
        second_row = db.query(DailyStrategyPerformance).filter(
            DailyStrategyPerformance.date == TODAY, DailyStrategyPerformance.contributor_name == "trend",
        ).first()

        assert second_count == first_count, "re-running the same date must not create duplicate rows"
        assert second_row.id == first_id, "the same scope must update the existing row, not insert a new one"
        assert second_row.correct == 1 and second_row.wrong == 1
    finally:
        _cleanup(db)
        db.close()


def test_v2_level_accuracy_reflects_the_decisions_own_signal():
    """DailyV2Performance scores the decision's own final_signal against the
    actual market outcome, not any one contributor's individual vote."""
    db = SessionLocal()
    try:
        _cleanup(db)
        decision_id = uuid.uuid4().hex
        _make_decision(db, decision_id, signal="LONG", execution_approved=True)
        _make_prediction(db, decision_id=decision_id, source_type="strategy", source_name="trend",
                          direction="LONG", lifecycle_status="RESOLVED_CORRECT")
        _make_prediction(db, decision_id=decision_id, source_type="quant", source_name="rsi",
                          direction="SHORT", lifecycle_status="RESOLVED_WRONG")
        db.commit()

        run_daily_aggregation(TODAY, db=db)

        from app.db.models import DailyV2Performance
        v2_row = db.query(DailyV2Performance).filter(
            DailyV2Performance.date == TODAY, DailyV2Performance.direction == "LONG",
        ).first()
        assert v2_row is not None
        # Exactly one V2-level row per decision (not per candidate) - two
        # candidates under one decision must not double-count the decision.
        assert v2_row.total_eligible_predictions == 1
        assert v2_row.execution_approved_count == 1
        assert v2_row.execution_blocked_count == 0
    finally:
        _cleanup(db)
        db.close()
