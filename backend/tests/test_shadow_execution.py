"""Shadow-mode execution guarantees (Bot Settings Part 5/9):
- a shadow-only indicator keeps computing real (non-zero) scores
- it is omitted from the active ensemble sum entirely, never added as zero
- shadow candidates flow through the same persistence/resolution pipeline
  as active ones (continue to resolve) without ever creating a Trade/order
"""
import ast
import inspect

import pytest

from app.db.models import IndicatorEligibility
from app.db.session import SessionLocal
from app.decision_engine import eligibility, indicator_performance, ledger
from app.decision_engine.v2 import ActiveDriveV2Engine
from tests.test_active_drive_v2 import _seed_resolved_predictions, legacy


def _clear_eligibility(db, source_name, symbol, timeframe):
    db.query(IndicatorEligibility).filter_by(source_name=source_name, symbol=symbol, timeframe=timeframe).delete()
    db.commit()


def test_shadow_only_indicator_keeps_real_nonzero_points_and_is_excluded_from_sum():
    db = SessionLocal()
    symbol, timeframe = "BTCUSDT", "30m"
    try:
        for i in range(25):
            _seed_resolved_predictions(db, source_name=f"noise-src-{i}", source_version="2.1.0", symbol=symbol,
                                        timeframe=timeframe, regime="test-regime", n=1, direction="LONG", win_ratio=1.0)

        context = {"db": db, "symbol": symbol, "timeframe": timeframe, "legacy": legacy(confidence=80),
                   "regime": "TRENDING", "data_status": "live", "risk_reward_ratio": 2.0, "settings_scope": "paper"}

        baseline = ActiveDriveV2Engine().evaluate(context)
        macd_candidate_before = next(c for c in baseline["candidates"] if c["name"] == "macd_momentum")
        assert macd_candidate_before["execution_mode"] == "ACTIVE"
        assert "macd_momentum" in baseline["active_indicators"]
        assert macd_candidate_before["final_points"] != 0
        assert "momentum" in baseline["family_totals"]

        db.add(IndicatorEligibility(source_name="macd_momentum", source_version="2.1.0", symbol=symbol,
                                     timeframe=timeframe, mode="paper", status="SHADOW_ONLY_POOR_PERFORMANCE"))
        db.commit()

        after = ActiveDriveV2Engine().evaluate(context)
        macd_candidate_after = next(c for c in after["candidates"] if c["name"] == "macd_momentum")

        # Real, non-zero score preserved for shadow performance tracking -
        # never zeroed out.
        assert macd_candidate_after["execution_mode"] == "SHADOW"
        assert macd_candidate_after["eligibility_status"] == "SHADOW_ONLY_POOR_PERFORMANCE"
        assert macd_candidate_after["final_points"] == pytest.approx(macd_candidate_before["final_points"])
        assert macd_candidate_after["final_points"] != 0
        assert "macd_momentum" in after["shadow_indicators"]
        assert "macd_momentum" not in after["active_indicators"]

        # Omitted from the sum entirely (no "momentum" family contribution
        # at all) rather than included as a zero.
        assert "momentum" not in after["family_totals"]
        assert after["long_points"] == pytest.approx(baseline["long_points"] - max(0, macd_candidate_before["final_points"]))
        assert after["short_points"] == pytest.approx(baseline["short_points"] - max(0, -macd_candidate_before["final_points"]))
    finally:
        _clear_eligibility(db, "macd_momentum", symbol, timeframe)
        db.close()


def test_manually_disabled_candidate_also_excluded_from_sum():
    db = SessionLocal()
    symbol, timeframe = "ETHUSDT", "1h"
    try:
        for i in range(25):
            _seed_resolved_predictions(db, source_name=f"noise-src2-{i}", source_version="2.1.0", symbol=symbol,
                                        timeframe=timeframe, regime="test-regime", n=1, direction="LONG", win_ratio=1.0)
        context = {"db": db, "symbol": symbol, "timeframe": timeframe, "legacy": legacy(confidence=80),
                   "regime": "TRENDING", "data_status": "live", "risk_reward_ratio": 2.0, "settings_scope": "paper"}
        db.add(IndicatorEligibility(source_name="macd_momentum", source_version="2.1.0", symbol=symbol,
                                     timeframe=timeframe, mode="paper", status="MANUALLY_DISABLED"))
        db.commit()
        result = ActiveDriveV2Engine().evaluate(context)
        macd_candidate = next(c for c in result["candidates"] if c["name"] == "macd_momentum")
        assert macd_candidate["execution_mode"] == "DISABLED"
        assert "macd_momentum" in result["disabled_indicators"]
        assert "macd_momentum" not in result["active_indicators"]
        assert "momentum" not in result["family_totals"]
    finally:
        _clear_eligibility(db, "macd_momentum", symbol, timeframe)
        db.close()


def test_shadow_predictions_structurally_cannot_create_orders():
    """The whole eligibility/ledger/indicator-performance pipeline must be
    structurally incapable of placing an order: none of these modules import
    anything that can execute a trade."""
    forbidden_modules = {"app.trading.execution_router", "app.trading.real_risk_gate", "app.trading.modes",
                         "app.exchanges.binance_futures_client"}
    for module in (eligibility, ledger, indicator_performance):
        tree = ast.parse(inspect.getsource(module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
        assert not (imported & forbidden_modules), f"{module.__name__} imports {imported & forbidden_modules}"


def test_shadow_candidates_persist_through_ledger_without_touching_trades():
    from app.core.config import settings as env_settings
    from app.db.models import ActiveDriveDecision, PredictionLedger, SignalCandidateRecord, Trade

    db = SessionLocal()
    symbol, timeframe = "BTCUSDT", "2h"
    try:
        for i in range(25):
            _seed_resolved_predictions(db, source_name=f"noise-src3-{i}", source_version="2.1.0", symbol=symbol,
                                        timeframe=timeframe, regime="test-regime", n=1, direction="LONG", win_ratio=1.0)
        db.add(IndicatorEligibility(source_name="macd_momentum", source_version="2.1.0", symbol=symbol,
                                     timeframe=timeframe, mode="paper", status="SHADOW_ONLY_POOR_PERFORMANCE"))
        db.commit()
        context = {"db": db, "symbol": symbol, "timeframe": timeframe, "legacy": legacy(confidence=80),
                   "regime": "TRENDING", "data_status": "live", "risk_reward_ratio": 2.0, "settings_scope": "paper"}
        result = ActiveDriveV2Engine().evaluate(context)
        trades_before = db.query(Trade).count()
        decision_id = ledger.persist(db, env_settings.admin_username, result, 100.0, {"price": 100.0})
        trades_after = db.query(Trade).count()
        assert trades_after == trades_before  # persisting a decision never creates a Trade

        candidate_row = db.query(SignalCandidateRecord).filter_by(decision_id=decision_id, source_name="macd_momentum").first()
        ledger_row = db.query(PredictionLedger).filter_by(decision_id=decision_id, source_name="macd_momentum").first()
        assert candidate_row.execution_mode == "SHADOW"
        assert ledger_row.execution_mode == "SHADOW"
        # A shadow ledger row still has a resolution_deadline and will flow
        # through the normal resolver like any other row - no separate
        # shadow-resolution pipeline exists or is needed.
        assert ledger_row.resolution_deadline is not None
        assert ledger_row.lifecycle_status == "PENDING"
    finally:
        _clear_eligibility(db, "macd_momentum", symbol, timeframe)
        db.close()
