import asyncio
import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import DecisionEngineChange, PredictionLedger, PredictionResolution, UserBotSetting
from app.decision_engine.ledger import persist
from app.decision_engine.repository import get_setting, performance, set_engine
from app.decision_engine.router import decision_engine_router
from app.decision_engine.types import DecisionEngineType
from app.decision_engine.v2 import (
    ActiveDriveV2Engine, EDGE_BLOCK_INSUFFICIENT_SAMPLES, EDGE_BLOCK_INVALID_LEVELS,
    EDGE_BLOCK_INVALID_PROBABILITY, EDGE_BLOCK_MISSING_ENTRY, EDGE_BLOCK_NET_EDGE_BELOW_THRESHOLD,
    _aggregate_edge_evidence, _current_edge,
)
from app.trading.execution_router import ExecutionRouter


def legacy(direction="LONG", confidence=80):
    return {"direction": direction, "confidence": confidence, "probability_up": 75, "probability_down": 25,
        "regime": "TRENDING", "strategies": {"trend": {"direction": direction, "confidence": confidence, "reason": "test"}},
        "ml_champion": {"used": False}, "features": {"price": 100, "ema20": 102, "ema50": 100, "trend_score": 2, "macd_hist": 0.1},
        "risk": {"allowed": True, "reason": "ok"}}


def test_v2_is_default_and_v1_remains_available():
    db = SessionLocal()
    try:
        row = get_setting(db, "new-user")
        assert row.decision_engine == "active_drive_v2"
        assert set(decision_engine_router.engines) == {DecisionEngineType.ACTIVE_DRIVE_V1, DecisionEngineType.ACTIVE_DRIVE_V2}
    finally: db.close()


def test_switch_is_atomic_and_audited():
    db = SessionLocal()
    try:
        set_engine(db, "switch-user", DecisionEngineType.ACTIVE_DRIVE_V1, "switch-user")
        assert get_setting(db, "switch-user").decision_engine == "active_drive_v1"
        audit = db.query(DecisionEngineChange).filter_by(user_id="switch-user").one()
        assert audit.previous_engine == "active_drive_v2"
        assert audit.new_engine == "active_drive_v1"
    finally: db.close()


def test_v2_small_sample_is_capped_and_fails_closed_without_edge():
    db = SessionLocal()
    try:
        result = ActiveDriveV2Engine().evaluate({"db": db, "symbol": "BTCUSDT", "timeframe": "5m", "legacy": legacy(),
            "regime": "TRENDING", "data_status": "live", "risk_reward_ratio": 2.0})
        assert result["final_signal"] == "NO_TRADE"
        assert result["eligible_for_execution"] is False
        assert result["directional_confidence"] is None
        assert result["decision_confidence"] is None
        assert 0 <= result["abstention_confidence"] <= 1
        assert result["required_confidence"] > 0
        assert result["engine_info"]["name"] == "Active Drive V2"
        assert {c["source_type"] for c in result["candidates"]} >= {"ml", "strategy", "quant"}
        assert all(abs(c["candidate_points"]) <= 4 for c in result["candidates"])
        assert all(c["historical_accuracy"] is None for c in result["candidates"])
        assert result["current_edge_supported"] is False
        assert result["edge_supported"] is False
        assert result["expected_edge"] is None
        assert result["edge_block_reason"] is not None
        assert any("Current edge is not supported" in reason for reason in result["blocking_reasons"])
    finally: db.close()


def test_v2_candidates_are_normalized_and_family_capped():
    db = SessionLocal()
    try:
        result = ActiveDriveV2Engine().evaluate({"db":db,"symbol":"BTCUSDT","timeframe":"15m","legacy":legacy(),"regime":"TRENDING","data_status":"live","risk_reward_ratio":2.0})
        required={"source_type","family","name","version","status","direction","calibrated_confidence","base_points","reliability_weight","final_points","resolved_samples","evidence_tier","reason"}
        assert result["candidates"]
        assert all(required <= set(candidate) for candidate in result["candidates"])
        assert all(abs(points) <= ActiveDriveV2Engine().version.count(".") * 0 + 12 for points in result["family_totals"].values())
        assert all(c["final_points"] == 0 for c in result["candidates"] if c["status"] == "shadow")
    finally:
        db.close()


def test_append_only_ledger_does_not_count_unresolved():
    db = SessionLocal()
    try:
        result = ActiveDriveV2Engine().evaluate({"db": db, "symbol": "ETHUSDT", "timeframe": "5m", "legacy": legacy(),
            "regime": "TRENDING", "data_status": "live", "risk_reward_ratio": 2.0})
        persist(db, "ledger-user", result, 100.0, legacy()["features"])
        assert db.query(PredictionLedger).filter_by(user_id="ledger-user").count() > 0
        assert db.query(PredictionResolution).count() == 0
        stats = performance(db, "ledger-user", "trend", "1.0.0", "ETHUSDT", "5m", "TRENDING")
        assert stats["resolved"] == 0 and stats["accuracy"] is None
    finally: db.close()


def test_inactive_engine_decision_rejected_before_provider(monkeypatch):
    db = SessionLocal()
    try: set_engine(db, "admin", DecisionEngineType.ACTIVE_DRIVE_V2, "admin")
    finally: db.close()
    router = ExecutionRouter()
    called = False
    async def forbidden(**kwargs):
        nonlocal called; called = True
    monkeypatch.setattr(router.provider(), "open_position", forbidden)
    result = asyncio.run(router.open_position(symbol="BTCUSDT", side="LONG", notional_usdt=10,
        decision_engine={"engine":"active_drive_v1", "engine_version":"1.0.0", "eligible_for_execution":True,
            "generated_at":datetime.now(timezone.utc).isoformat()}))
    assert result.reason == "HORIZON_AUTHORITY_REQUIRED"
    assert called is False


def test_v2_exception_does_not_fallback(monkeypatch):
    db = SessionLocal()
    try:
        get_setting(db, "failure-user")
        engine = decision_engine_router.engines[DecisionEngineType.ACTIVE_DRIVE_V2]
        monkeypatch.setattr(engine, "evaluate", lambda context: (_ for _ in ()).throw(RuntimeError("boom")))
        result = decision_engine_router.evaluate(db, "failure-user", {"symbol":"BTCUSDT", "timeframe":"5m", "legacy":legacy()})
        assert result["engine"] == "active_drive_v2"
        assert result["final_signal"] == "NO_TRADE"
        assert "failed closed" in result["blocking_reasons"][0]
    finally: db.close()


# --- Current-edge calculation (Phase C/D/E) -------------------------------

def _seed_resolved_predictions(db, *, source_name, source_version, symbol, timeframe, regime, n,
                               direction="LONG", win_return=0.02, loss_return=0.01, win_ratio=0.6):
    """Seeds real PredictionLedger/PredictionResolution rows the same shape
    app.decision_engine.ledger.persist() and app.decision_engine.resolver
    produce. actual_return follows resolver.py's convention: the raw,
    unsigned (close - reference_price) / reference_price - a correct SHORT
    has a *negative* actual_return even though it won."""
    user_id = settings.admin_username
    wins = round(n * win_ratio)
    now = datetime.now(timezone.utc)
    for i in range(n):
        is_win = i < wins
        prediction_id = uuid.uuid4().hex
        raw_return = win_return if is_win else -loss_return
        unsigned_actual_return = raw_return if direction == "LONG" else -raw_return
        db.add(PredictionLedger(prediction_id=prediction_id, candidate_id=uuid.uuid4().hex, decision_id=uuid.uuid4().hex,
            user_id=user_id, engine="active_drive_v2", engine_version="2.2.0", source_type="strategy",
            source_name=source_name, source_version=source_version, symbol=symbol, timeframe=timeframe,
            market_regime=regime, direction=direction, probability_up=0.7, probability_down=0.3, confidence=70,
            points=2.0, expected_edge=None, reference_price=100.0, target_reference_price=102.0,
            stop_reference_price=99.0, data_revision="test", target_horizon_seconds=1500,
            resolution_deadline=now, feature_snapshot_hash="test", generated_at=now))
        db.add(PredictionResolution(prediction_id=prediction_id, actual_return=unsigned_actual_return,
            resolved_direction=direction if is_win else ("SHORT" if direction == "LONG" else "LONG"),
            correct=is_win, neutral_result=False, resolution_reason="test", resolved_at=now))
    db.commit()


def test_performance_sign_adjusts_short_wins():
    """A correct SHORT (price dropped) must read as a positive win return,
    not a negative one - the pre-fix bug mixed raw unsigned actual_return
    into average_win_return regardless of direction."""
    db = SessionLocal()
    try:
        _seed_resolved_predictions(db, source_name="sign-test-short", source_version="1.0.0", symbol="BTCUSDT",
            timeframe="5m", regime="test-regime", n=10, direction="SHORT", win_return=0.02, loss_return=0.01, win_ratio=1.0)
        stats = performance(db, settings.admin_username, "sign-test-short", "1.0.0", "BTCUSDT", "5m", "test-regime")
        assert stats["resolved"] == 10
        assert stats["average_win_return"] == pytest.approx(0.02)
        assert stats["realized_edge"] == pytest.approx(0.02)
    finally: db.close()


def test_performance_sign_adjusts_long_and_mixed_directions():
    db = SessionLocal()
    try:
        _seed_resolved_predictions(db, source_name="sign-test-long", source_version="1.0.0", symbol="BTCUSDT",
            timeframe="5m", regime="test-regime", n=10, direction="LONG", win_return=0.03, loss_return=0.01, win_ratio=0.5)
        stats = performance(db, settings.admin_username, "sign-test-long", "1.0.0", "BTCUSDT", "5m", "test-regime")
        assert stats["average_win_return"] == pytest.approx(0.03)
        assert stats["average_loss_return"] == pytest.approx(-0.01)
    finally: db.close()


def _profitable_evidence(n=30, win_return=0.03, loss_return=0.01):
    return {"resolved": n, "average_win_return": win_return, "average_loss_return": -loss_return,
            "evidence_timestamp": datetime.now(timezone.utc).isoformat()}


def test_current_edge_supported_for_profitable_setup():
    result = _current_edge("LONG", entry=100.0, target=103.0, stop=99.0, probability_up=0.65,
        edge_evidence=_profitable_evidence())
    assert result["supported"] is True
    assert result["block_reason"] is None
    assert result["net_edge"] > 0
    assert result["gross_edge"] > result["net_edge"]  # costs strictly reduce gross -> net


def test_current_edge_short_direction_is_symmetric():
    result = _current_edge("SHORT", entry=100.0, target=97.0, stop=101.0, probability_up=0.35,
        edge_evidence=_profitable_evidence())
    assert result["supported"] is True
    assert result["net_edge"] > 0


def test_current_edge_blocked_missing_levels():
    assert _current_edge("LONG", None, 103.0, 99.0, 0.65, _profitable_evidence())["block_reason"] == EDGE_BLOCK_MISSING_ENTRY
    assert _current_edge("LONG", 100.0, 103.0, None, 0.65, _profitable_evidence())["block_reason"] == "missing_stop"
    assert _current_edge("LONG", 100.0, None, 99.0, 0.65, _profitable_evidence())["block_reason"] == "missing_target"


def test_current_edge_blocked_invalid_levels_wrong_side_of_entry():
    # target below entry for a LONG makes reward_distance negative - never a real edge.
    result = _current_edge("LONG", entry=100.0, target=98.0, stop=99.0, probability_up=0.65, edge_evidence=_profitable_evidence())
    assert result["block_reason"] == EDGE_BLOCK_INVALID_LEVELS


def test_current_edge_blocked_invalid_probability():
    result = _current_edge("LONG", 100.0, 103.0, 99.0, probability_up=None, edge_evidence=_profitable_evidence())
    assert result["block_reason"] == EDGE_BLOCK_INVALID_PROBABILITY
    result = _current_edge("LONG", 100.0, 103.0, 99.0, probability_up=1.2, edge_evidence=_profitable_evidence())
    assert result["block_reason"] == EDGE_BLOCK_INVALID_PROBABILITY


def test_current_edge_blocked_insufficient_samples():
    thin_evidence = _profitable_evidence(n=3)
    result = _current_edge("LONG", 100.0, 103.0, 99.0, 0.65, thin_evidence)
    assert result["block_reason"] == EDGE_BLOCK_INSUFFICIENT_SAMPLES
    assert result["supported"] is False


def test_current_edge_blocked_when_costs_erode_thin_edge():
    # A barely-profitable gross edge that estimated round-trip costs (fees +
    # spread + slippage) push at or below the net-edge threshold.
    thin_evidence = {"resolved": 30, "average_win_return": 0.0015, "average_loss_return": -0.0015,
                     "evidence_timestamp": datetime.now(timezone.utc).isoformat()}
    result = _current_edge("LONG", entry=100.0, target=100.15, stop=99.85, probability_up=0.51, edge_evidence=thin_evidence)
    assert result["supported"] is False
    assert result["block_reason"] == EDGE_BLOCK_NET_EDGE_BELOW_THRESHOLD
    assert result["net_edge"] <= settings.active_drive_min_net_edge


def test_current_edge_blocked_expectancy_unavailable():
    result = _current_edge("LONG", 100.0, 103.0, 99.0, 0.65,
        {"resolved": 30, "average_win_return": None, "average_loss_return": None, "evidence_timestamp": None})
    assert result["block_reason"] == "expectancy_unavailable"


def test_current_edge_no_trade_direction_blocks():
    result = _current_edge("NO_TRADE", 100.0, 103.0, 99.0, 0.65, _profitable_evidence())
    assert result["block_reason"] == "no_trade_direction"


def test_aggregate_edge_evidence_is_sample_weighted():
    sources = [
        {"resolved_samples": 10, "average_win_return": 0.02, "average_loss_return": -0.01, "evidence_timestamp": "2026-01-01T00:00:00+00:00"},
        {"resolved_samples": 30, "average_win_return": 0.04, "average_loss_return": -0.02, "evidence_timestamp": "2026-01-02T00:00:00+00:00"},
    ]
    combined = _aggregate_edge_evidence(sources)
    assert combined["resolved"] == 40
    # weighted toward the 30-sample source, not a naive 50/50 mean of 0.02 and 0.04
    assert combined["average_win_return"] == pytest.approx((0.02 * 10 + 0.04 * 30) / 40)
    assert combined["evidence_timestamp"] == "2026-01-02T00:00:00+00:00"


def test_aggregate_edge_evidence_empty_is_unsupported():
    combined = _aggregate_edge_evidence([])
    assert combined["resolved"] == 0
    assert combined["average_win_return"] is None
    assert combined["average_loss_return"] is None
