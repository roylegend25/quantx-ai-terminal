"""Both directional pathways of Active Drive V2 must be reachable end-to-end
through evaluate() on seeded, resolved out-of-sample history - no confidence
bypass, no threshold override. Guards against the class of regression where
an unconditional blocker (or a starved gate) silently makes NO_TRADE the only
possible output regardless of evidence."""
import uuid
from datetime import datetime, timezone

from app.core.config import settings
from app.db.models import PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine.v2 import ActiveDriveV2Engine

# regime_for() maps these context regimes to these exact evidence labels.
LONG_REGIME, LONG_LABEL = "TRENDING", "Normal-volatility bullish trend"
SHORT_REGIME, SHORT_LABEL = "BEARISH", "Normal-volatility bearish trend"


def _seed(db, *, source_name, symbol, regime_label, direction, n=25, win_ratio=0.64):
    user_id = settings.admin_username
    wins = round(n * win_ratio)
    now = datetime.now(timezone.utc)
    for i in range(n):
        is_win = i < wins
        prediction_id = uuid.uuid4().hex
        raw_return = 0.02 if is_win else -0.008
        unsigned = raw_return if direction == "LONG" else -raw_return
        db.add(PredictionLedger(prediction_id=prediction_id, candidate_id=uuid.uuid4().hex,
            decision_id=uuid.uuid4().hex, user_id=user_id, engine="active_drive_v2",
            engine_version="2.2.0", source_type="strategy", source_name=source_name,
            source_version="1.0.0", symbol=symbol, timeframe="5m", market_regime=regime_label,
            direction=direction, probability_up=0.7, probability_down=0.3, confidence=70,
            points=2.0, expected_edge=None, reference_price=100.0, target_reference_price=102.0,
            stop_reference_price=99.0, data_revision="test", target_horizon_seconds=1500,
            resolution_deadline=now, feature_snapshot_hash="test", generated_at=now))
        db.add(PredictionResolution(prediction_id=prediction_id, actual_return=unsigned,
            resolved_direction=direction if is_win else ("SHORT" if direction == "LONG" else "LONG"),
            correct=is_win, neutral_result=False, resolution_reason="test", resolved_at=now))
    db.commit()


def _cleanup(db, symbol):
    ids = [r[0] for r in db.query(PredictionLedger.prediction_id).filter(
        PredictionLedger.symbol == symbol).all()]
    if ids:
        db.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
        db.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        db.commit()


def _legacy(direction, target, stop):
    # Minimal features: only the legacy "trend" strategy votes directionally,
    # so it is the sole eligible source and the calibration minimum applies
    # to exactly the bucket seeded above.
    return {"direction": direction, "confidence": 82, "probability_up": 75, "probability_down": 25,
            "price": 100.0, "target": target, "stop": stop,
            "strategies": {"trend": {"direction": direction, "confidence": 82, "reason": "test"}},
            "ml_champion": {"used": False}, "features": {},
            "risk": {"allowed": True, "reason": "ok"}}


def _evaluate(db, symbol, regime, legacy):
    return ActiveDriveV2Engine().evaluate({
        "db": db, "symbol": symbol, "timeframe": "5m", "legacy": legacy,
        "regime": regime, "data_status": "live", "risk_reward_ratio": 2.0})


def test_long_pathway_reachable_without_overrides():
    db = SessionLocal()
    symbol = "REACHLONGUSDT"
    try:
        _seed(db, source_name="trend", symbol=symbol, regime_label=LONG_LABEL, direction="LONG")
        result = _evaluate(db, symbol, LONG_REGIME, _legacy("LONG", target=103.0, stop=99.0))
        assert result["final_signal"] == "LONG", result["blocking_reasons"]
        assert result["eligible_for_execution"] is True
        assert result["blocking_reasons"] == []
        assert result["directional_confidence"] is not None
        assert result["directional_confidence"] >= settings.active_drive_min_confidence
        assert result["current_edge_supported"] is True
        assert result["net_expected_edge"] is not None and result["net_expected_edge"] > 0
        gates = result["decision_metrics"]
        assert all(gates[name]["passed"] for name in ("evidence", "point_margin", "history", "confidence", "edge"))
    finally:
        _cleanup(db, symbol)
        db.close()


def test_short_pathway_reachable_without_overrides():
    db = SessionLocal()
    symbol = "REACHSHORTUSDT"
    try:
        _seed(db, source_name="trend", symbol=symbol, regime_label=SHORT_LABEL, direction="SHORT")
        result = _evaluate(db, symbol, SHORT_REGIME, _legacy("SHORT", target=97.0, stop=101.0))
        assert result["final_signal"] == "SHORT", result["blocking_reasons"]
        assert result["eligible_for_execution"] is True
        assert result["blocking_reasons"] == []
        assert result["current_edge_supported"] is True
        assert result["net_expected_edge"] is not None and result["net_expected_edge"] > 0
    finally:
        _cleanup(db, symbol)
        db.close()


def test_no_trade_pathway_keeps_signal_separate_from_execution():
    """A NO_TRADE from source disagreement stays a signal-layer abstention:
    the execution block reason mirrors the signal blocker, and no execution
    concern (balance, notional, lease) ever appears in the signal blockers."""
    db = SessionLocal()
    symbol = "REACHSPLITUSDT"
    try:
        _seed(db, source_name="trend", symbol=symbol, regime_label=LONG_LABEL, direction="LONG")
        legacy = _legacy("LONG", target=103.0, stop=99.0)
        # A second eligible source voting the other way drops directional
        # separation below the threshold - a genuine disagreement abstention.
        legacy["strategies"]["momentum"] = {"direction": "SHORT", "confidence": 78, "reason": "test"}
        _seed(db, source_name="momentum", symbol=symbol, regime_label=LONG_LABEL, direction="SHORT")
        result = _evaluate(db, symbol, LONG_REGIME, legacy)
        assert result["final_signal"] == "NO_TRADE"
        assert any("confidence" in reason.lower() for reason in result["blocking_reasons"])
        # "margin" alone would false-positive on the signal-layer "point
        # margin" gate; only execution-layer concerns are forbidden here.
        forbidden = ("balance", "account margin", "isolated margin", "notional", "lease", "unlock")
        assert not any(any(word in reason.lower() for word in forbidden)
                       for reason in result["blocking_reasons"])
    finally:
        _cleanup(db, symbol)
        db.close()
