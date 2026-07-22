"""ExecutionRouter.open_position() integration with the single-
authoritative-decision model (decision_id=, app.decision_engine.
execution_gate) - replaces the horizon_decision_id= integration tests
removed from test_horizon_authority.py. Same guarantees: no provider call
without a valid decision, exactly-once execution, full provenance
carried through, position sizing respects configured limits, a sizing
failure never resolves the provider."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import ActiveDriveDecision, ActiveDriveDecisionConsumption, Portfolio, Trade, UserBotSetting
from app.db.session import SessionLocal
from app.risk import settings_repository
from app.trading.execution_router import ExecutionRouter, PositionSizingError
from app.trading_horizon.idempotency import durable_intents

from tests.test_horizon_authority import NoRedis, Provider

USER = "v2-router-test-user"
SYMBOL = "BTCUSDT"
TF = "15m"


@pytest.fixture(autouse=True)
def clean_state():
    db = SessionLocal()
    db.query(ActiveDriveDecisionConsumption).delete()
    db.query(ActiveDriveDecision).filter(ActiveDriveDecision.user_id == USER).delete()
    db.query(Trade).filter(Trade.user_id == USER).delete()
    row = db.get(UserBotSetting, USER) or UserBotSetting(user_id=USER)
    row.trading_profile = "mid_term"
    db.add(row)
    portfolio = db.get(Portfolio, 1) or Portfolio(id=1)
    portfolio.balance = 10000
    portfolio.equity = 10000
    portfolio.daily_pnl = 0
    db.add(portfolio)
    db.commit()
    db.close()
    durable_intents._client = NoRedis()
    yield


def _approved_decision(db, **overrides) -> ActiveDriveDecision:
    from app.decision_engine.execution_gate import finalize_decision_for_execution
    defaults = dict(
        decision_id=f"v2router-{datetime.now(timezone.utc).timestamp()}", user_id=USER, engine="active_drive_v2",
        engine_version="2.2.0", symbol=SYMBOL, timeframe=TF, signal="LONG", confidence=0.8,
        expected_edge=0.02, edge_supported=True, eligible_for_execution=True, blocking_reasons=[],
        decision_payload={"recommended_stop": 95.0, "recommended_target": 110.0, "reference_price": 100.0,
                         "required_confidence": 0.6},
        shadow=False, created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    row = ActiveDriveDecision(**defaults)
    db.add(row)
    db.commit()
    finalize_decision_for_execution(db, row.decision_id)
    db.refresh(row)
    return row


def test_missing_decision_id_is_rejected(monkeypatch):
    router = ExecutionRouter()
    provider = Provider()
    monkeypatch.setattr(router, "provider", lambda: provider)
    result = asyncio.run(router.open_position(symbol=SYMBOL, side="LONG", notional_usdt=10))
    assert result.reason == "V2_DECISION_REQUIRED"
    assert provider.entries == 0


def test_no_provider_call_without_a_valid_decision(monkeypatch):
    calls = 0

    def forbidden():
        nonlocal calls
        calls += 1
        raise AssertionError("provider must not resolve")

    router = ExecutionRouter()
    monkeypatch.setattr(router, "provider", forbidden)
    result = asyncio.run(router.open_position(symbol=SYMBOL, side="LONG", notional_usdt=10,
                                              decision_id="fabricated-nonexistent", user_id=USER))
    assert result.reason == "DECISION_NOT_FOUND"
    assert calls == 0


def test_fully_approved_decision_reaches_provider_exactly_once(monkeypatch):
    db = SessionLocal()
    decision = _approved_decision(db)
    db.close()

    router = ExecutionRouter()
    provider = Provider()
    monkeypatch.setattr(router, "provider", lambda: provider)

    result = asyncio.run(router.open_position(symbol=SYMBOL, side="LONG", user_id=USER,
                                              decision_id=decision.decision_id))
    assert result.ok is True, result.reason
    assert provider.entries == 1

    kwargs = provider.last_kwargs
    de = kwargs["decision_engine"]
    assert de["engine"] == "active_drive_v2"
    assert de["decision_id"] == decision.decision_id
    assert de["horizon_decision_id"] == decision.decision_id
    assert kwargs["confidence"] is not None

    # exactly-once: a second attempt against the same (now-consumed)
    # decision must not reach the provider again.
    repeat = asyncio.run(router.open_position(symbol=SYMBOL, side="LONG", user_id=USER,
                                              decision_id=decision.decision_id))
    assert repeat.ok is False
    assert repeat.reason == "DECISION_ALREADY_CONSUMED"
    assert provider.entries == 1


def test_expired_decision_is_rejected(monkeypatch):
    db = SessionLocal()
    decision = _approved_decision(db, created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    db.close()
    router = ExecutionRouter()
    provider = Provider()
    monkeypatch.setattr(router, "provider", lambda: provider)
    result = asyncio.run(router.open_position(symbol=SYMBOL, user_id=USER, decision_id=decision.decision_id))
    assert result.reason == "DECISION_EXPIRED"
    assert provider.entries == 0


def test_direction_and_symbol_mismatch_are_rejected(monkeypatch):
    db = SessionLocal()
    decision = _approved_decision(db)
    db.close()
    router = ExecutionRouter()
    provider = Provider()
    monkeypatch.setattr(router, "provider", lambda: provider)
    assert asyncio.run(router.open_position(symbol=SYMBOL, side="SHORT", user_id=USER,
                                            decision_id=decision.decision_id)).reason == "DECISION_DIRECTION_MISMATCH"
    assert asyncio.run(router.open_position(symbol="ETHUSDT", user_id=USER,
                                            decision_id=decision.decision_id)).reason == "DECISION_SYMBOL_MISMATCH"
    assert provider.entries == 0


def test_position_sizing_respects_configured_user_limit(monkeypatch):
    settings_repository.update_settings({"max_position_size_usd": 500.0})
    try:
        db = SessionLocal()
        decision = _approved_decision(db)
        db.close()
        router = ExecutionRouter()
        provider = Provider()
        monkeypatch.setattr(router, "provider", lambda: provider)
        monkeypatch.setattr("app.trading_horizon.sizing.settings.binance_max_notional_per_trade", 25.0)
        result = asyncio.run(router.open_position(symbol=SYMBOL, user_id=USER, decision_id=decision.decision_id))
        assert result.ok is True, result.reason
        assert provider.last_kwargs["notional_usdt"] == 500.0
    finally:
        settings_repository.update_settings({"max_position_size_usd": 1000.0})


def test_sizing_failure_never_resolves_the_provider(monkeypatch):
    calls = 0

    def forbidden():
        nonlocal calls
        calls += 1
        raise AssertionError("provider must not resolve")

    def unavailable(*args, **kwargs):
        raise PositionSizingError("RISK_POLICY_UNAVAILABLE")

    db = SessionLocal()
    decision = _approved_decision(db)
    db.close()
    router = ExecutionRouter()
    monkeypatch.setattr("app.trading.execution_router.calculate_position_size", unavailable)
    monkeypatch.setattr(router, "provider", forbidden)
    result = asyncio.run(router.open_position(symbol=SYMBOL, user_id=USER, decision_id=decision.decision_id))
    assert result.reason == "RISK_POLICY_UNAVAILABLE"
    assert calls == 0


def test_unapproved_decision_never_resolves_the_provider(monkeypatch):
    calls = 0

    def forbidden():
        nonlocal calls
        calls += 1
        raise AssertionError("provider must not resolve")

    db = SessionLocal()
    decision = _approved_decision(db, signal="NO_TRADE", eligible_for_execution=False,
                                  blocking_reasons=["Insufficient total evidence"])
    db.close()
    router = ExecutionRouter()
    monkeypatch.setattr(router, "provider", forbidden)
    result = asyncio.run(router.open_position(symbol=SYMBOL, user_id=USER, decision_id=decision.decision_id))
    assert result.reason == "Insufficient total evidence"
    assert calls == 0
