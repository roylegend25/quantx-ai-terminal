"""Point-margin gate: calculation, scoped persistence, and the boundary
condition (Bot Settings Part 2). Point margin and confidence are always
two separate blockers, never merged."""
import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import settings as env_settings
from app.db.models import ActiveDriveDecision
from app.db.session import SessionLocal
from app.decision_engine.ledger import persist
from app.decision_engine.v2 import ActiveDriveV2Engine
from app.risk import settings_repository
from tests.test_active_drive_v2 import _seed_resolved_predictions, legacy


def test_point_margin_worked_example_from_spec():
    """The exact example from the Bot Settings spec: LONG=68, SHORT=61 ->
    margin=7. Required=8 rejects; required=6 passes. This is a direct check
    of the formula v2.py's gate uses (abs(long_points - short_points) >=
    required), independent of how the ensemble happens to produce a
    particular pair of scores."""
    long_score, short_score = 68, 61
    margin = abs(long_score - short_score)
    assert margin == 7
    assert not (margin >= 8)
    assert margin >= 6


def test_point_margin_calculation_matches_long_short_points():
    db = SessionLocal()
    try:
        for i in range(25):
            _seed_resolved_predictions(db, source_name=f"pm-calc-src-{i % 3}", source_version="2.1.0",
                                        symbol="BTCUSDT", timeframe="5m", regime="test-regime", n=1,
                                        direction="LONG", win_ratio=1.0)
        result = ActiveDriveV2Engine().evaluate({
            "db": db, "symbol": "BTCUSDT", "timeframe": "5m", "legacy": legacy(confidence=90),
            "regime": "TRENDING", "data_status": "live", "risk_reward_ratio": 2.0, "settings_scope": "paper",
        })
        assert result["point_margin"] == pytest.approx(abs(result["long_points"] - result["short_points"]), abs=1e-6)
        assert result["configuration_scope"] == "paper"
        assert isinstance(result["configuration_version"], int)
    finally:
        db.close()


def test_point_margin_boundary_equal_to_required_passes():
    db = SessionLocal()
    try:
        for i in range(25):
            _seed_resolved_predictions(db, source_name=f"pm-boundary-src-{i % 3}", source_version="2.1.0",
                                        symbol="ETHUSDT", timeframe="15m", regime="test-regime", n=1,
                                        direction="LONG", win_ratio=1.0)
        context = {"db": db, "symbol": "ETHUSDT", "timeframe": "15m", "legacy": legacy(confidence=90),
                   "regime": "TRENDING", "data_status": "live", "risk_reward_ratio": 2.0, "settings_scope": "paper"}
        baseline = ActiveDriveV2Engine().evaluate(context)
        margin = baseline["point_margin"]

        settings_repository.update_settings({"min_point_margin": margin}, scope="paper", changed_by="tester", db=db)
        at_boundary = ActiveDriveV2Engine().evaluate(context)
        assert at_boundary["point_margin_pass"] is True
        assert not any("Point margin" in b for b in at_boundary["blocking_reasons"])

        settings_repository.update_settings({"min_point_margin": margin + 0.5}, scope="paper", changed_by="tester", db=db)
        above_margin = ActiveDriveV2Engine().evaluate(context)
        assert above_margin["point_margin_pass"] is False
        assert any("Point margin" in b for b in above_margin["blocking_reasons"])
    finally:
        settings_repository.reset_settings(scope="paper", changed_by="tester")
        db.close()


def test_point_margin_and_confidence_are_independent_blockers():
    """Raising min_point_margin to an unreachable value (while
    min_confidence_to_trade is untouched) must add the point-margin blocker
    without changing whether the confidence gate itself passes or fails -
    they are separate `if`/`elif` checks in v2.py, never merged into one
    combined condition."""
    db = SessionLocal()
    try:
        for i in range(25):
            _seed_resolved_predictions(db, source_name=f"pm-indep-src-{i % 3}", source_version="2.1.0",
                                        symbol="BTCUSDT", timeframe="1h", regime="test-regime", n=1,
                                        direction="LONG", win_ratio=1.0)
        context = {"db": db, "symbol": "BTCUSDT", "timeframe": "1h", "legacy": legacy(confidence=90),
                   "regime": "TRENDING", "data_status": "live", "risk_reward_ratio": 2.0, "settings_scope": "paper"}
        baseline = ActiveDriveV2Engine().evaluate(context)
        confidence_passed_before = baseline["decision_metrics"]["confidence"]["passed"]
        assert not any("Point margin" in b for b in baseline["blocking_reasons"])

        # Make point margin effectively impossible to clear (near the top of
        # its valid 0-50 range, far above anything this ensemble can
        # realistically produce) - min_confidence_to_trade is untouched.
        settings_repository.update_settings(
            {"min_point_margin": 45.0}, scope="paper", changed_by="tester", db=db)
        result = ActiveDriveV2Engine().evaluate(context)

        assert any("Point margin" in b for b in result["blocking_reasons"])
        assert result["decision_metrics"]["confidence"]["passed"] == confidence_passed_before
        assert result["decision_metrics"]["confidence"]["required"] == baseline["decision_metrics"]["confidence"]["required"]
    finally:
        settings_repository.reset_settings(scope="paper", changed_by="tester")
        db.close()


def test_decision_persists_point_margin_fields():
    db = SessionLocal()
    try:
        for i in range(25):
            _seed_resolved_predictions(db, source_name=f"pm-persist-src-{i % 3}", source_version="2.1.0",
                                        symbol="BTCUSDT", timeframe="4h", regime="test-regime", n=1,
                                        direction="LONG", win_ratio=1.0)
        context = {"db": db, "symbol": "BTCUSDT", "timeframe": "4h", "legacy": legacy(confidence=90),
                   "regime": "TRENDING", "data_status": "live", "risk_reward_ratio": 2.0, "settings_scope": "paper"}
        result = ActiveDriveV2Engine().evaluate(context)
        decision_id = persist(db, env_settings.admin_username, result, 100.0, {"price": 100.0})
        row = db.get(ActiveDriveDecision, decision_id)
        assert row.point_margin == pytest.approx(result["point_margin"])
        assert row.required_point_margin == pytest.approx(result["required_point_margin"])
        assert row.point_margin_pass == result["point_margin_pass"]
        assert row.configuration_scope == "paper"
        assert row.configuration_version == result["configuration_version"]
        assert isinstance(row.active_indicators, list)
        assert isinstance(row.exclusion_reasons, dict)
    finally:
        db.close()
