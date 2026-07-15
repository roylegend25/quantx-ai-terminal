import asyncio
from datetime import datetime, timezone
import pytest
from sqlalchemy.exc import IntegrityError
from app.db.session import SessionLocal
from app.db.models import DecisionEngineChange, PredictionLedger, PredictionResolution, UserBotSetting
from app.decision_engine.ledger import persist
from app.decision_engine.repository import get_setting, performance, set_engine
from app.decision_engine.router import decision_engine_router
from app.decision_engine.types import DecisionEngineType
from app.decision_engine.v2 import ActiveDriveV2Engine
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
        assert "Expected edge is not yet supported by resolved out-of-sample history" in result["blocking_reasons"]
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
        stats = performance(db, "trend", "1.0.0", "ETHUSDT", "5m", "TRENDING")
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
    assert result.reason == "ENGINE_NOT_ACTIVE"
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
