"""Isolated paper smoke test (deterministic fixtures, no real market data,
no threshold changes) - the 3 scenarios required by the Decision/Execution
Pipeline fix:

  A) LONG candidate, confidence passes, edge fails -> the persisted V2
     decision is never execution_approved; no execution request is ever
     created; the exact edge blocker is reported.
  B) Fully valid LONG (trade levels valid, edge supported, execution
     approved) -> exactly one paper execution request -> one paper order,
     with full decision provenance attached; a second attempt against the
     same (now-consumed) decision is refused, never a duplicate.
  C) The same fully-valid decision replayed against Binance Real (mode
     locked because BINANCE_LIVE_ENABLED=false) -> blocked at the live
     authorization gate, zero calls into the real client - never the
     generic "no actionable signal" message.

Trading Horizon removal: uses app.decision_engine.execution_gate
(finalize_decision_for_execution/validate_decision_for_consumption)
directly against ActiveDriveDecision instead of a separate Horizon
authority object - no live prediction pipeline, no network."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.db.models import ActiveDriveDecision, ActiveDriveDecisionConsumption, UserBotSetting
from app.db.session import SessionLocal
from app.decision_engine.execution_gate import finalize_decision_for_execution
from app.trading import modes
from app.trading.execution_router import ExecutionRouter

from tests.test_horizon_authority import Provider, setup_user

USER = "smoke-scenario-user"
SYMBOL = "BTCUSDT"
TF = "15m"


@pytest.fixture(autouse=True)
def clean_state():
    setup_user(USER)
    modes.set_mode(modes.MODE_PAPER)
    db = SessionLocal()
    db.query(ActiveDriveDecisionConsumption).delete()
    db.query(ActiveDriveDecision).filter(ActiveDriveDecision.user_id == USER).delete()
    row = db.get(UserBotSetting, USER) or UserBotSetting(user_id=USER)
    row.trading_profile = "mid_term"
    db.add(row)
    db.commit()
    db.close()
    yield
    modes.set_mode(modes.MODE_PAPER)


def _decision(**overrides) -> ActiveDriveDecision:
    defaults = dict(
        decision_id=f"smoke-{datetime.now(timezone.utc).timestamp()}", user_id=USER, engine="active_drive_v2",
        engine_version="2.2.0", symbol=SYMBOL, timeframe=TF, signal="LONG", confidence=0.8,
        expected_edge=0.02, edge_supported=True, eligible_for_execution=True, blocking_reasons=[],
        decision_payload={"recommended_stop": 95.0, "recommended_target": 110.0, "reference_price": 100.0,
                         "required_confidence": 0.6},
        shadow=False,
    )
    defaults.update(overrides)
    db = SessionLocal()
    row = ActiveDriveDecision(**defaults)
    db.add(row)
    db.commit()
    finalized = finalize_decision_for_execution(db, row.decision_id)
    db.refresh(finalized)
    for column in ActiveDriveDecision.__table__.columns:
        getattr(finalized, column.name)
    db.expunge(finalized)
    db.close()
    return finalized


def test_scenario_a_edge_blocked_creates_no_execution_request():
    row = _decision(edge_supported=False, edge_block_reason="NEGATIVE_EXPECTED_VALUE")
    assert row.execution_approved is False, "an edge-blocked decision must never be execution_approved"
    assert row.final_block_reason == "NEGATIVE_EXPECTED_VALUE"

    router = ExecutionRouter()
    provider = Provider()
    import unittest.mock
    with unittest.mock.patch.object(router, "provider", lambda: provider):
        result = asyncio.run(router.open_position(symbol=SYMBOL, user_id=USER, decision_id=row.decision_id))
    assert result.ok is False
    assert provider.entries == 0, "no execution request/order may exist when the edge gate failed"


def test_scenario_b_fully_valid_long_creates_exactly_one_paper_request(monkeypatch):
    decision = _decision()
    assert decision.execution_approved is True
    router = ExecutionRouter()
    provider = Provider()
    monkeypatch.setattr(router, "provider", lambda: provider)

    result = asyncio.run(router.open_position(symbol=SYMBOL, user_id=USER, decision_id=decision.decision_id))
    assert result.ok is True
    assert provider.entries == 1

    sent = provider.last_kwargs["decision_engine"]
    assert sent["execution_mode"] == "automatic"
    assert sent["horizon_decision_id"] == decision.decision_id
    assert sent["decision_id"] == decision.decision_id
    assert sent["edge_at_entry"] is not None

    # The same (now-consumed) decision must never produce a second entry -
    # restart-safe, no duplicate paper order.
    result2 = asyncio.run(router.open_position(symbol=SYMBOL, user_id=USER, decision_id=decision.decision_id))
    assert result2.ok is False
    assert provider.entries == 1


def test_scenario_c_binance_real_dry_run_blocked_before_client():
    assert settings.binance_live_enabled is False
    decision = _decision()
    assert decision.execution_approved is True

    modes.set_mode(modes.MODE_LIVE)
    assert modes.effective_mode() == modes.MODE_LIVE_LOCKED

    class _RaisingClient:
        def __init__(self, *a, **kw):
            raise AssertionError("Binance client must never be constructed while live trading is disabled")

    from app.trading.execution_router import router as real_execution_router
    with patch("app.exchanges.binance_futures_client.BinanceFuturesClient", _RaisingClient):
        result = asyncio.run(real_execution_router.open_position(symbol=SYMBOL, user_id=USER,
                                                                  decision_id=decision.decision_id))

    assert result.ok is False
    assert result.mode == modes.MODE_LIVE_LOCKED
    assert result.reason != "Model has not produced an actionable signal"
    assert real_execution_router._live is None
