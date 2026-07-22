"""Isolated paper smoke test (deterministic fixtures, no real market data,
no threshold changes) - the 3 scenarios required by the Decision/Execution
Pipeline fix:

  A) LONG candidate, confidence passes, edge fails -> no execution
     request is ever created; the exact edge blocker is reported.
  B) Fully valid LONG (trade levels valid, edge supported, authority
     granted, risk approved) -> exactly one paper execution request ->
     one paper order, with full decision/authority provenance attached;
     a second attempt against the same (now-consumed) authority is
     refused, never a duplicate.
  C) The same fully-valid decision replayed against Binance Real (mode
     locked because BINANCE_LIVE_ENABLED=false) -> blocked at the live
     authorization gate, zero calls into the real client - never the
     generic "no actionable signal" message.

Uses the same synthetic-fixture technique as test_horizon_authority.py
(frames()/build_horizon_decision/persist_horizon_decision) - no live
prediction pipeline, no network."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.db.models import ActiveDriveDecision, TradingHorizonDecision
from app.db.session import SessionLocal
from app.trading import modes
from app.trading_horizon.authority import HorizonAuthorityError, persist_horizon_decision
from app.trading_horizon.service import build_horizon_decision
from app.trading.execution_router import ExecutionRouter

from tests.test_horizon_authority import Provider, authority, frames, persisted_decision, setup_user

USER = "smoke-scenario-user"


@pytest.fixture(autouse=True)
def clean_state():
    setup_user(USER)
    modes.set_mode(modes.MODE_PAPER)
    yield
    modes.set_mode(modes.MODE_PAPER)


def test_scenario_a_edge_blocked_creates_no_execution_request():
    values = frames()
    values["15m"]["current_edge_supported"] = False
    values["15m"]["edge_supported"] = False

    db = SessionLocal()
    for tf, item in values.items():
        if tf == "1M":
            continue
        item["decision_id"] = f"scenario-a-{tf}-{datetime.now(timezone.utc).timestamp()}"
        db.add(ActiveDriveDecision(
            decision_id=item["decision_id"], user_id=USER, engine=item["engine"],
            engine_version=item["engine_version"], symbol="BTCUSDT", timeframe=tf, signal=item["final_signal"],
            confidence=item["confidence"], expected_edge=item.get("expected_edge"),
            edge_supported=item.get("edge_supported"),
            eligible_for_execution=item["eligible_for_execution"], decision_payload=item, shadow=False,
        ))
    db.commit()

    decision = build_horizon_decision("BTCUSDT", values, "mid_term", user_id=USER, engine_version="2.2.0")
    assert decision["current_edge_supported"] is False, "fixture must genuinely fail the edge gate"

    with pytest.raises(HorizonAuthorityError):
        persist_horizon_decision(db, user_id=USER, policy=decision, timeframe_decisions=values, profile_revision=1)

    assert db.query(TradingHorizonDecision).filter_by(user_id=USER).count() == 0, (
        "no execution request/authority may exist when the edge gate failed"
    )
    db.close()


def test_scenario_b_fully_valid_long_creates_exactly_one_paper_request(monkeypatch):
    decision = persisted_decision(user=USER)
    router = ExecutionRouter()
    provider = Provider()
    monkeypatch.setattr(router, "provider", lambda: provider)

    result = asyncio.run(router.open_position(**authority(decision)))
    assert result.ok is True
    assert provider.entries == 1

    sent = provider.last_kwargs["decision_engine"]
    assert sent["execution_mode"] == "automatic"
    assert sent["horizon_decision_id"] == decision["profile_decision_id"]
    assert sent["decision_id"]
    assert sent["edge_at_entry"] is not None

    # The same (now-consumed) authority must never produce a second entry -
    # restart-safe, no duplicate paper order.
    result2 = asyncio.run(router.open_position(**authority(decision)))
    assert result2.ok is False
    assert provider.entries == 1


def test_scenario_c_binance_real_dry_run_blocked_before_client(monkeypatch):
    assert settings.binance_live_enabled is False
    decision = persisted_decision(user=USER)

    modes.set_mode(modes.MODE_LIVE)
    assert modes.effective_mode() == modes.MODE_LIVE_LOCKED

    class _RaisingClient:
        def __init__(self, *a, **kw):
            raise AssertionError("Binance client must never be constructed while live trading is disabled")

    from app.trading.execution_router import router as real_execution_router
    with patch("app.exchanges.binance_futures_client.BinanceFuturesClient", _RaisingClient):
        result = asyncio.run(real_execution_router.open_position(**authority(decision)))

    assert result.ok is False
    assert result.mode == modes.MODE_LIVE_LOCKED
    assert result.reason != "Model has not produced an actionable signal"
    assert real_execution_router._live is None
