"""Star (recommended-for-reactivation) rule and manual reactivation
(Bot Settings Part 6/7): requires >=20 shadow results, meaningful hit
rate/expectancy, guards against one isolated winning trade, never
auto-reactivates, and manual reactivation for Binance Real never touches
live-trading state."""
import ast
import inspect
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.indicator_control as indicator_control_module
from app.core.config import settings as env_settings
from app.core.security import create_access_token
from app.db.models import IndicatorEligibility, IndicatorEligibilityHistory, PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import indicator_notifications
from app.decision_engine.indicator_performance import evaluate_indicator

USER = env_settings.admin_username


def _seed_shadow(db, *, source_name, symbol="BTCUSDT", timeframe="5m", n_correct, n_wrong,
                  win_return=0.01, loss_return=0.01, shadow_since=None):
    now = datetime.now(timezone.utc)
    total = n_correct + n_wrong
    # Spread wins evenly across the whole sequence (rather than clustering
    # them all at the oldest or newest end) so a legitimately-qualifying
    # aggregate also reads consistently in the most-recent subwindow -
    # avoids the test itself accidentally looking like "deteriorated
    # recently" or "isolated win concentrated in one spot".
    win_positions = {round(j * total / n_correct) for j in range(n_correct)} if n_correct else set()
    for i in range(total):
        is_win = i in win_positions
        prediction_id = uuid.uuid4().hex
        gen_at = now - timedelta(minutes=(total - i))
        db.add(PredictionLedger(
            prediction_id=prediction_id, candidate_id=uuid.uuid4().hex, decision_id=uuid.uuid4().hex,
            user_id=USER, engine="active_drive_v2", engine_version="2.2.0", source_type="strategy",
            source_name=source_name, source_version="1.0.0", symbol=symbol, timeframe=timeframe,
            market_regime="test", direction="LONG", probability_up=0.7, probability_down=0.3, confidence=70,
            points=0.0, reference_price=100.0, target_reference_price=102.0, stop_reference_price=99.0,
            data_revision="test", target_horizon_seconds=300, resolution_deadline=gen_at,
            feature_snapshot_hash="test", generated_at=gen_at, lifecycle_status="RESOLVED_CORRECT" if is_win else "RESOLVED_WRONG",
            execution_mode="SHADOW",
        ))
        db.add(PredictionResolution(
            prediction_id=prediction_id, actual_return=win_return if is_win else -loss_return,
            resolved_direction="LONG", correct=is_win, neutral_result=False, resolution_reason="test", resolved_at=gen_at,
            net_direction_adjusted_return=win_return if is_win else -loss_return,
            maximum_adverse_excursion=0.005,
        ))
    db.commit()


def _make_eligibility(db, source_name, symbol, timeframe, mode, status, shadow_since=None):
    row = IndicatorEligibility(source_name=source_name, source_version="1.0.0", symbol=symbol, timeframe=timeframe,
                                mode=mode, status=status, shadow_since=shadow_since or datetime.now(timezone.utc) - timedelta(days=30))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _cleanup(db, source_name, symbol, timeframe):
    for mode in ("paper", "binance_real"):
        row = db.query(IndicatorEligibility).filter_by(source_name=source_name, symbol=symbol, timeframe=timeframe, mode=mode).first()
        if row:
            db.query(IndicatorEligibilityHistory).filter_by(eligibility_id=row.id).delete()
            db.delete(row)
    db.query(PredictionLedger).filter_by(source_name=source_name, symbol=symbol, timeframe=timeframe).delete()
    db.query(indicator_notifications.IndicatorNotification).filter_by(source_name=source_name).delete()
    db.commit()


def test_star_requires_minimum_twenty_shadow_results():
    db = SessionLocal()
    name = "star-test-19"
    try:
        _make_eligibility(db, name, "BTCUSDT", "5m", "paper", "SHADOW_ONLY_POOR_PERFORMANCE")
        _seed_shadow(db, source_name=name, n_correct=14, n_wrong=5)  # 19 total, otherwise-qualifying stats
        evaluate_indicator(db, name, "1.0.0", "BTCUSDT", "5m")
        row = db.query(IndicatorEligibility).filter_by(source_name=name, mode="paper").first()
        assert row.status == "SHADOW_ONLY_POOR_PERFORMANCE"  # not starred yet
    finally:
        _cleanup(db, name, "BTCUSDT", "5m")
        db.close()


def test_star_awarded_with_sufficient_positive_evidence():
    db = SessionLocal()
    name = "star-test-pass"
    try:
        _make_eligibility(db, name, "BTCUSDT", "5m", "paper", "SHADOW_ONLY_POOR_PERFORMANCE")
        _seed_shadow(db, source_name=name, n_correct=14, n_wrong=6, win_return=0.02, loss_return=0.01)
        evaluate_indicator(db, name, "1.0.0", "BTCUSDT", "5m")
        row = db.query(IndicatorEligibility).filter_by(source_name=name, mode="paper").first()
        assert row.status == "RECOMMENDED_FOR_REACTIVATION"
        notifications = indicator_notifications.list_notifications(db=db)["notifications"]
        assert any(n["source_name"] == name and n["event"] == "recommended_for_reactivation" for n in notifications)
    finally:
        _cleanup(db, name, "BTCUSDT", "5m")
        db.close()


def test_isolated_winning_trade_does_not_qualify():
    """Two big wins among mostly losses can produce a positive aggregate net
    expectancy but must not qualify - hit rate and the recent-subwindow
    requirement both guard against a result driven by one/two trades."""
    db = SessionLocal()
    name = "star-test-isolated"
    try:
        _make_eligibility(db, name, "BTCUSDT", "5m", "paper", "SHADOW_ONLY_POOR_PERFORMANCE")
        _seed_shadow(db, source_name=name, n_correct=2, n_wrong=18, win_return=0.50, loss_return=0.01)
        evaluate_indicator(db, name, "1.0.0", "BTCUSDT", "5m")
        row = db.query(IndicatorEligibility).filter_by(source_name=name, mode="paper").first()
        assert row.status == "SHADOW_ONLY_POOR_PERFORMANCE"  # never starred
    finally:
        _cleanup(db, name, "BTCUSDT", "5m")
        db.close()


def test_star_notification_deduped_on_repeat_evaluation():
    db = SessionLocal()
    name = "star-test-dedup"
    try:
        _make_eligibility(db, name, "BTCUSDT", "5m", "paper", "SHADOW_ONLY_POOR_PERFORMANCE")
        _seed_shadow(db, source_name=name, n_correct=14, n_wrong=6, win_return=0.02, loss_return=0.01)
        evaluate_indicator(db, name, "1.0.0", "BTCUSDT", "5m")
        evaluate_indicator(db, name, "1.0.0", "BTCUSDT", "5m")  # idempotent re-run, same evaluation_version
        notifications = [n for n in indicator_notifications.list_notifications(db=db)["notifications"] if n["source_name"] == name]
        assert len(notifications) == 1
    finally:
        _cleanup(db, name, "BTCUSDT", "5m")
        db.close()


def test_never_auto_reactivated_even_after_starring():
    db = SessionLocal()
    name = "star-test-no-auto"
    try:
        _make_eligibility(db, name, "BTCUSDT", "5m", "paper", "SHADOW_ONLY_POOR_PERFORMANCE")
        _seed_shadow(db, source_name=name, n_correct=14, n_wrong=6, win_return=0.02, loss_return=0.01)
        evaluate_indicator(db, name, "1.0.0", "BTCUSDT", "5m")
        row = db.query(IndicatorEligibility).filter_by(source_name=name, mode="paper").first()
        assert row.status == "RECOMMENDED_FOR_REACTIVATION"
        # Re-evaluating again (e.g. the next scheduler tick) must never flip
        # this to ACTIVE by itself.
        evaluate_indicator(db, name, "1.0.0", "BTCUSDT", "5m")
        db.refresh(row)
        assert row.status == "RECOMMENDED_FOR_REACTIVATION"
    finally:
        _cleanup(db, name, "BTCUSDT", "5m")
        db.close()


def _make_client():
    app = FastAPI()
    app.include_router(indicator_control_module.router)
    return TestClient(app)


def _auth_headers():
    return {"Authorization": f"Bearer {create_access_token(env_settings.admin_username)}"}


def test_manual_paper_reactivation_via_api():
    db = SessionLocal()
    name = "star-test-manual-paper"
    try:
        row = _make_eligibility(db, name, "BTCUSDT", "5m", "paper", "RECOMMENDED_FOR_REACTIVATION")
        client = _make_client()
        resp = client.post(f"/api/indicators/{row.id}/action", json={"action": "enable_paper", "reason": "looks good"},
                            headers=_auth_headers())
        assert resp.status_code == 200
        db.refresh(row)
        assert row.status == "ACTIVE"
    finally:
        _cleanup(db, name, "BTCUSDT", "5m")
        db.close()


def test_manual_binance_real_reactivation_never_touches_live_trading():
    db = SessionLocal()
    name = "star-test-manual-real"
    try:
        row = _make_eligibility(db, name, "BTCUSDT", "5m", "binance_real", "RECOMMENDED_FOR_REACTIVATION")
        client = _make_client()
        resp = client.post(f"/api/indicators/{row.id}/action", json={"action": "enable_binance_real"},
                            headers=_auth_headers())
        assert resp.status_code == 200
        db.refresh(row)
        assert row.status == "ACTIVE"

        # Structural guarantee: the router module never imports anything
        # that could unlock/lease/kill-switch live execution.
        tree = ast.parse(inspect.getsource(indicator_control_module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
        assert "app.trading.modes" not in imported
    finally:
        _cleanup(db, name, "BTCUSDT", "5m")
        db.close()
