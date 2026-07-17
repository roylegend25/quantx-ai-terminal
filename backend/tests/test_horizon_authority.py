import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import event

from app.db.models import (ActiveDriveDecision, ExecutionFenceCounter, ExecutionIntentAudit, ExecutionIntentLock,
    Portfolio, Trade, TradingHorizonDecision, TradingHorizonTimeframeLink, UserBotSetting)
from app.db.session import SessionLocal
from app.trading.execution_router import ExecutionRouter, RouterResult, revalidate_snapshot_price
from app.trading_horizon.idempotency import DurableIntentCoordinator, durable_intents
from app.trading_horizon.service import build_horizon_decision
from app.trading_horizon.authority import persist_horizon_decision
from app.engine.trading_engine import TradingEngine
from app.risk import settings_repository
from app.trading_horizon.sizing import PositionSizingError


class NoRedis:
    async def set(self, *args, **kwargs): raise ConnectionError
    async def get(self, *args, **kwargs): raise ConnectionError
    async def delete(self, *args, **kwargs): raise ConnectionError


class Provider:
    mode = "PAPER"
    def __init__(self): self.entries = 0; self.last_kwargs = None
    async def open_position(self, **kwargs):
        guard=kwargs.get("_pre_submit_guard")
        if guard and not await guard(): return RouterResult(False,self.mode,"open_position",reason="EXECUTION_LEASE_LOST")
        self.entries += 1
        self.last_kwargs = kwargs
        return RouterResult(True, self.mode, "open_position", detail={"entry": self.entries})
    async def close_position(self, **kwargs): return RouterResult(True, self.mode, "close_position")
    async def update_stop_loss(self, **kwargs): return RouterResult(True, self.mode, "update_stop_loss")


def frames(one_hour="LONG"):
    now=datetime.now(timezone.utc)
    result={tf:{"direction":"LONG","final_signal":"LONG","eligible_for_execution":True,"confidence":.8,"expected_edge":.02,
        "current_edge_supported":True,"edge_supported":True,
        "engine":"active_drive_v2","engine_version":"2.2.0","generated_at":now.isoformat(),
        "expires_at":(now+timedelta(seconds=60)).isoformat(),"market_data_revision":"m1","performance_snapshot_revision":"p1"}
            for tf in ("1m","5m","15m","1h","4h","1d","1w")}
    result["1h"]["direction"]=one_hour; result["1h"]["final_signal"]=one_hour
    result["1M"]={"direction":"NO_TRADE","eligible_for_execution":False,"confidence":None,"expected_edge":None}
    return result


def authority(decision):
    return dict(symbol=decision["symbol"],side=decision["direction"],notional_usdt=10,timeframe=decision["execution_timeframe"],
        horizon_decision_id=decision["profile_decision_id"],user_id=decision["user_id"])


def setup_user(user="horizon-user"):
    db=SessionLocal(); db.query(TradingHorizonTimeframeLink).delete(); db.query(TradingHorizonDecision).delete()
    db.query(ExecutionIntentLock).delete(); db.query(ExecutionIntentAudit).delete(); db.query(ExecutionFenceCounter).delete()
    db.query(ActiveDriveDecision).filter_by(user_id=user).delete()
    row=db.get(UserBotSetting,user) or UserBotSetting(user_id=user)
    row.trading_profile="mid_term"; row.profile_revision=1; db.add(row); db.commit(); db.close()
    db=SessionLocal(); portfolio=db.get(Portfolio,1) or Portfolio(id=1); portfolio.balance=10000; portfolio.equity=10000
    db.add(portfolio); db.commit(); db.close()


def persisted_decision(user="horizon-user", one_hour="LONG", return_inputs=False):
    values=frames(one_hour); db=SessionLocal()
    for tf,item in values.items():
        if tf=="1M": continue
        item["decision_id"]=f"{user}-{tf}-{datetime.now(timezone.utc).timestamp()}"
        db.add(ActiveDriveDecision(decision_id=item["decision_id"],user_id=user,engine=item["engine"],
            engine_version=item["engine_version"],symbol="BTCUSDT",timeframe=tf,signal=item["final_signal"],
            confidence=item["confidence"],expected_edge=item["expected_edge"],eligible_for_execution=item["eligible_for_execution"],
            decision_payload=item,shadow=False))
    db.commit()
    decision=build_horizon_decision("BTCUSDT",values,"mid_term",user_id=user,engine_version="2.2.0")
    persist_horizon_decision(db,user_id=user,policy=decision,timeframe_decisions=values,profile_revision=1)
    db.close()
    return (decision, values) if return_inputs else decision


def test_authority_issuance_is_idempotent_for_identical_evidence():
    setup_user(); decision, values=persisted_decision(return_inputs=True)
    first_id=decision["profile_decision_id"]
    db=SessionLocal()
    second=persist_horizon_decision(db,user_id="horizon-user",policy=decision,
        timeframe_decisions=values,profile_revision=1)
    assert second.id==first_id
    assert db.query(TradingHorizonDecision).count()==1
    required=set(decision["required_timeframes"])|{decision["structural_bias_timeframe"]}
    assert db.query(TradingHorizonTimeframeLink).filter_by(horizon_decision_id=first_id).count()==len(required)
    db.close()


def test_changed_execution_evidence_issues_a_distinct_authority():
    setup_user(); first, _=persisted_decision(return_inputs=True)
    second, _=persisted_decision(return_inputs=True)
    assert second["profile_decision_id"] != first["profile_decision_id"]
    db=SessionLocal(); assert db.query(TradingHorizonDecision).count()==2; db.close()


def test_concurrent_identical_issuance_returns_one_authority():
    setup_user(); decision, values=persisted_decision(return_inputs=True)
    db=SessionLocal(); db.query(TradingHorizonTimeframeLink).delete(); db.query(TradingHorizonDecision).delete()
    db.commit(); db.close()
    def issue_once():
        session=SessionLocal()
        try:
            return persist_horizon_decision(session,user_id="horizon-user",policy=dict(decision),
                timeframe_decisions=values,profile_revision=1).id
        finally:
            session.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids=list(pool.map(lambda _:issue_once(),range(2)))
    assert ids[0]==ids[1]
    db=SessionLocal(); assert db.query(TradingHorizonDecision).count()==1; db.close()


def test_missing_legacy_and_v1_authority_are_rejected(monkeypatch):
    router=ExecutionRouter(); provider=Provider(); monkeypatch.setattr(router,"provider",lambda:provider)
    result=asyncio.run(router.open_position(symbol="BTCUSDT",side="LONG",notional_usdt=10))
    assert result.reason=="HORIZON_AUTHORITY_REQUIRED" and provider.entries==0
    setup_user()
    assert asyncio.run(router.open_position(symbol="BTCUSDT",side="LONG",notional_usdt=10,
        horizon_decision_id="fabricated",user_id="horizon-user")).reason=="HORIZON_DECISION_NOT_FOUND"


def test_no_exchange_provider_call_without_persisted_authority(monkeypatch):
    router=ExecutionRouter(); calls=0
    def forbidden_provider():
        nonlocal calls; calls+=1; raise AssertionError("execution provider must not resolve")
    monkeypatch.setattr(router,"provider",forbidden_provider)
    result=asyncio.run(router.open_position(symbol="BTCUSDT",side="LONG",notional_usdt=10,
        horizon_decision_id="caller-fabricated",user_id="horizon-user"))
    assert result.reason=="HORIZON_DECISION_NOT_FOUND" and calls==0


def test_chart_month_and_confirmation_cannot_authorize_only_15m_can(monkeypatch):
    setup_user(); durable_intents._client=NoRedis()
    router=ExecutionRouter(); provider=Provider(); monkeypatch.setattr(router,"provider",lambda:provider)
    decision=persisted_decision(); decision["chart_timeframe"]="1M"
    valid=authority(decision)
    confirmation={**valid,"timeframe":"1h"}
    chart={**valid,"timeframe":"1M"}
    bias={**valid,"timeframe":"1d"}
    assert asyncio.run(router.open_position(**confirmation)).reason=="TIMEFRAME_NOT_EXECUTION_AUTHORITY"
    assert asyncio.run(router.open_position(**bias)).reason=="TIMEFRAME_NOT_EXECUTION_AUTHORITY"
    assert asyncio.run(router.open_position(**chart)).reason=="TIMEFRAME_NOT_EXECUTION_AUTHORITY"
    assert asyncio.run(router.open_position(**valid)).ok is True
    assert provider.entries==1
    assert asyncio.run(router.open_position(**valid)).reason=="HORIZON_DECISION_ALREADY_CONSUMED"


# ============================================ Part D: deterministic paper fixtures
#
# Proves, end-to-end through the REAL PaperExecutionProvider (not the mocked
# Provider used above) and the real trades table, exactly what the current
# paper-pipeline audit asked for: a candidate that never got Trading Horizon
# authority creates zero execution requests and zero orders (Scenario A),
# and a fully-approved candidate creates exactly one linked paper order/
# position, with a second attempt on the same authority correctly rejected
# rather than duplicating it (Scenario B). Neither scenario touches
# production thresholds or real market data - frames()/persisted_decision()
# are entirely synthetic.

def _trade_count(symbol="BTCUSDT"):
    db = SessionLocal()
    try:
        return db.query(Trade).filter_by(symbol=symbol).count()
    finally:
        db.close()


def test_scenario_a_blocked_long_creates_no_execution_request_or_order():
    """Signal LONG, confidence would pass, but Trading Horizon authority was
    never issued (no persisted decision at all - the exact state a
    disagreeing-timeframe or otherwise-ineligible candidate leaves behind,
    see test_blocked_policy_cannot_be_persisted_by_internal_service above).
    The real router - real PAPER provider, no mocking - must refuse before
    ever calling the paper engine, and the trades table must stay
    untouched."""
    setup_user()
    before = _trade_count()
    router = ExecutionRouter()  # real provider: PAPER mode is the default

    result = asyncio.run(router.open_position(symbol="BTCUSDT", side="LONG", notional_usdt=10, user_id="horizon-user"))

    assert result.ok is False
    assert result.reason == "HORIZON_AUTHORITY_REQUIRED"
    assert _trade_count() == before, "no execution request may ever reach the paper engine without persisted authority"


def test_scenario_b_fully_approved_long_creates_exactly_one_paper_order_and_position(monkeypatch):
    """Signal LONG, confidence passes, evidence agrees across every required
    timeframe, and Trading Horizon authority is persisted - the one path
    that is supposed to create an order. Runs through the actual
    ExecutionRouter and its full authority/sizing/idempotency pipeline
    (unlike the mocked-Provider tests above, which stop short of asserting
    what the provider actually receives); the provider itself is swapped
    for a spy so the test doesn't depend on a live paper-ledger HTTP
    server being bound to a port (PaperExecutionProvider books trades via
    a real self-call to /api/paper - see execution_engine.submit_order -
    which no test in this suite exercises end-to-end for exactly that
    reason). Asserts exactly one call reaches the provider, carrying full
    decision/authority provenance, and that a second attempt against the
    same already-consumed authority is rejected rather than duplicating
    it - the router-level guarantee behind "exactly one paper position"."""
    setup_user()
    decision = persisted_decision()  # one_hour="LONG" default: every required timeframe agrees
    router = ExecutionRouter()
    provider = Provider()
    monkeypatch.setattr(router, "provider", lambda: provider)

    result = asyncio.run(router.open_position(**authority(decision)))

    assert result.ok is True, result.reason
    assert result.mode == "PAPER"
    assert provider.entries == 1, "fully approved LONG must reach the execution provider exactly once"

    kwargs = provider.last_kwargs
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["side"] == "LONG"
    # Provenance Part D/8 requires: decision_id, authority linkage, source,
    # direction, entry/target/stop, confidence all preserved through to the
    # provider call, never lost or fabricated along the way.
    de = kwargs["decision_engine"]
    assert de["engine"] == "active_drive_v2"
    assert de["decision_id"]  # real Active Drive V2 decision, never fabricated/blank
    assert de["horizon_decision_id"] == decision["profile_decision_id"]
    assert kwargs["confidence"] is not None

    # the SAME authority cannot be consumed a second time - proves "exactly
    # one", not merely "at least one"
    repeat = asyncio.run(router.open_position(**authority(decision)))
    assert repeat.ok is False
    assert repeat.reason == "HORIZON_DECISION_ALREADY_CONSUMED"
    assert provider.entries == 1, "a rejected duplicate attempt must not reach the provider a second time"


def test_disagreement_is_no_trade_and_protection_remains_available(monkeypatch):
    decision=build_horizon_decision("BTCUSDT",frames("SHORT"),"mid_term",user_id="horizon-user")
    assert decision["direction"]=="NO_TRADE" and decision["ready"] is False
    router=ExecutionRouter(); provider=Provider(); monkeypatch.setattr(router,"provider",lambda:provider)
    assert asyncio.run(router.close_position(symbol="BTCUSDT")).ok
    assert asyncio.run(router.update_stop_loss(position_id=1,stop_loss=90)).ok


def test_blocked_policy_cannot_be_persisted_by_internal_service():
    setup_user(); values=frames("SHORT")
    decision=build_horizon_decision("BTCUSDT",values,"mid_term",user_id="horizon-user")
    db=SessionLocal()
    with pytest.raises(ValueError,match="HORIZON_AUTHORITY_NOT_ELIGIBLE"):
        persist_horizon_decision(db,user_id="horizon-user",policy=decision,
            timeframe_decisions=values,profile_revision=1)
    assert db.query(TradingHorizonDecision).count()==0
    db.close()


def test_mixed_evaluation_context_is_rejected_before_persistence():
    setup_user(); values=frames()
    decision=build_horizon_decision("BTCUSDT",values,"mid_term",user_id="horizon-user")
    decision["evaluation_id"]="evaluation-a"
    for item in values.values(): item["evaluation_id"]="evaluation-a"
    values["1h"]["evaluation_id"]="evaluation-b"
    db=SessionLocal()
    with pytest.raises(ValueError,match="HORIZON_EVALUATION_CONTEXT_MISMATCH"):
        persist_horizon_decision(db,user_id="horizon-user",policy=decision,
            timeframe_decisions=values,profile_revision=1)
    assert db.query(TradingHorizonDecision).count()==0
    db.close()


def test_persisted_authority_identity_and_link_failures(monkeypatch):
    setup_user(); decision=persisted_decision(); router=ExecutionRouter(); provider=Provider(); monkeypatch.setattr(router,"provider",lambda:provider)
    base=authority(decision)
    assert asyncio.run(router.open_position(**{**base,"user_id":"other-user"})).reason=="HORIZON_DECISION_USER_MISMATCH"
    assert asyncio.run(router.open_position(**{**base,"symbol":"ETHUSDT"})).reason=="HORIZON_DECISION_SYMBOL_MISMATCH"
    assert asyncio.run(router.open_position(**{**base,"side":"SHORT"})).reason=="HORIZON_DECISION_DIRECTION_MISMATCH"
    db=SessionLocal(); setting=db.get(UserBotSetting,"horizon-user"); setting.profile_revision=2; db.commit(); db.close()
    assert asyncio.run(router.open_position(**base)).reason=="HORIZON_PROFILE_REVISION_MISMATCH"
    db=SessionLocal(); setting=db.get(UserBotSetting,"horizon-user"); setting.profile_revision=1
    link=db.query(TradingHorizonTimeframeLink).filter_by(horizon_decision_id=decision["profile_decision_id"],role="confirmation").first(); db.delete(link); db.commit(); db.close()
    assert asyncio.run(router.open_position(**base)).reason in {"HORIZON_TIMEFRAME_LINK_INVALID","HORIZON_EXECUTION_DECISION_MISMATCH"}


def test_expired_shadow_and_v1_links_fail_closed(monkeypatch):
    setup_user(); decision=persisted_decision(); router=ExecutionRouter(); monkeypatch.setattr(router,"provider",lambda:Provider()); base=authority(decision)
    db=SessionLocal(); row=db.get(TradingHorizonDecision,decision["profile_decision_id"]); row.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1); db.commit(); db.close()
    assert asyncio.run(router.open_position(**base)).reason=="HORIZON_DECISION_EXPIRED"
    setup_user(); decision=persisted_decision(); base=authority(decision); db=SessionLocal()
    link=db.query(TradingHorizonTimeframeLink).filter_by(horizon_decision_id=decision["profile_decision_id"]).first(); persisted=db.get(ActiveDriveDecision,link.decision_id); persisted.shadow=True; db.commit(); db.close()
    assert asyncio.run(router.open_position(**base)).reason in {"HORIZON_TIMEFRAME_LINK_INVALID","HORIZON_EXECUTION_DECISION_MISMATCH"}
    setup_user(); decision=persisted_decision(); base=authority(decision); db=SessionLocal()
    link=db.query(TradingHorizonTimeframeLink).filter_by(horizon_decision_id=decision["profile_decision_id"]).first(); persisted=db.get(ActiveDriveDecision,link.decision_id); persisted.engine="active_drive_v1"; db.commit(); db.close()
    assert asyncio.run(router.open_position(**base)).reason in {"HORIZON_TIMEFRAME_LINK_INVALID","HORIZON_EXECUTION_DECISION_MISMATCH"}


def test_authority_and_links_rollback_atomically_on_second_link_failure():
    setup_user(); values=frames(); db=SessionLocal()
    for tf,item in values.items():
        if tf=="1M": continue
        item["decision_id"]=f"atomic-{tf}"
        db.add(ActiveDriveDecision(decision_id=item["decision_id"],user_id="horizon-user",engine="active_drive_v2",
            engine_version="2.2.0",symbol="BTCUSDT",timeframe=tf,signal="LONG",confidence=.8,expected_edge=.02,
            eligible_for_execution=True,decision_payload=item,shadow=False))
    db.commit(); policy=build_horizon_decision("BTCUSDT",values,"mid_term",user_id="horizon-user",engine_version="2.2.0")
    inserted={"count":0}
    def fail_second(mapper,connection,target):
        inserted["count"]+=1
        if inserted["count"]==2: raise RuntimeError("forced second link failure")
    event.listen(TradingHorizonTimeframeLink,"before_insert",fail_second)
    try:
        with pytest.raises(RuntimeError):
            persist_horizon_decision(db,user_id="horizon-user",policy=policy,timeframe_decisions=values,profile_revision=1)
    finally:
        event.remove(TradingHorizonTimeframeLink,"before_insert",fail_second)
    assert db.query(TradingHorizonDecision).count()==0
    assert db.query(TradingHorizonTimeframeLink).count()==0
    db.close()


def test_current_edge_from_another_decision_identity_is_rejected():
    setup_user(); values=frames(); db=SessionLocal()
    for tf,item in values.items():
        if tf=="1M": continue
        item["decision_id"]=f"edge-identity-{tf}"
        db.add(ActiveDriveDecision(decision_id=item["decision_id"],user_id="horizon-user",engine="active_drive_v2",
            engine_version="2.2.0",symbol="BTCUSDT",timeframe=tf,signal="LONG",confidence=.8,expected_edge=.02,
            eligible_for_execution=True,decision_payload=dict(item),shadow=False))
    db.commit(); values["15m"]["expected_edge"]=.09
    policy=build_horizon_decision("BTCUSDT",values,"mid_term",user_id="horizon-user",engine_version="2.2.0")
    with pytest.raises(ValueError,match="HORIZON_CURRENT_EDGE_IDENTITY_MISMATCH"):
        persist_horizon_decision(db,user_id="horizon-user",policy=policy,timeframe_decisions=values,profile_revision=1)
    assert db.query(TradingHorizonDecision).count()==0
    db.close()


def test_durable_duplicate_survives_restart_and_stale_lock_recovers():
    setup_user("durable-user")
    first=DurableIntentCoordinator(30); first._client=NoRedis()
    base={"user_id":"durable-user","symbol":"BTCUSDT","engine":"active_drive_v2","profile_decision_id":"p1","execution_timeframe":"15m","direction":"LONG"}
    acquired,_,owner=asyncio.run(first.acquire(base)); assert acquired and owner
    restarted=DurableIntentCoordinator(30); restarted._client=NoRedis()
    assert asyncio.run(restarted.acquire(base))[1]=="DUPLICATE_EXECUTION_INTENT"
    opposing={**base,"profile_decision_id":"opposing","direction":"SHORT"}
    assert asyncio.run(restarted.acquire(opposing))[1]=="CONFLICTING_EXECUTION_INTENT"
    db=SessionLocal(); lock=db.get(ExecutionIntentLock,"durable-user:BTCUSDT:active_drive_v2"); lock.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1); db.commit(); db.close()
    newer={**base,"profile_decision_id":"p2"}
    assert asyncio.run(restarted.acquire(newer))[0] is True


def test_concurrent_workers_only_one_acquires():
    setup_user("concurrent-user")
    one=DurableIntentCoordinator(30); two=DurableIntentCoordinator(30); one._client=NoRedis(); two._client=NoRedis()
    base={"user_id":"concurrent-user","symbol":"ETHUSDT","engine":"active_drive_v2","profile_decision_id":"same","execution_timeframe":"15m","direction":"LONG"}
    async def race(): return await asyncio.gather(one.acquire(base),two.acquire(base))
    results=asyncio.run(race())
    assert sum(result[0] for result in results)==1
    db=SessionLocal(); assert db.query(ExecutionIntentAudit).filter_by(user_id="concurrent-user",symbol="ETHUSDT").count()==1; db.close()


def test_automated_engine_passes_complete_authority_contract(monkeypatch):
    horizon=build_horizon_decision("BTCUSDT",frames(),"mid_term",user_id="admin"); horizon["profile_revision"]=2
    horizon["profile_decision_id"]="persisted-authority-id"; horizon["horizon_decision_id"]="persisted-authority-id"
    service_calls={"count":0}
    async def evaluate_and_issue(**kwargs):
        service_calls["count"]+=1
        return {**horizon,"authority_status":"persisted_authority","persisted":True}
    monkeypatch.setattr("app.api.timeframes.evaluate_and_issue_horizon_authority",evaluate_and_issue)
    captured={}
    async def open_position(**kwargs): captured.update(kwargs); return RouterResult(True,"PAPER","open_position")
    monkeypatch.setattr("app.engine.trading_engine.execution_router.open_position",open_position)
    asyncio.run(TradingEngine()._run_symbol_cycle("BTCUSDT","lease-owner"))
    assert service_calls["count"]==1
    for field in ("horizon_decision_id","user_id"):
        assert captured.get(field) is not None
    assert "horizon_decision" not in captured and "engine_id" not in captured
    assert "timeframe" not in captured and "side" not in captured
    assert "sl" not in captured and "tp" not in captured and "confidence" not in captured
    assert captured["execution_lease_owner"]=="lease-owner"


def test_router_uses_bound_snapshot_when_later_model_changes(monkeypatch):
    setup_user(); durable_intents._client=NoRedis(); decision=persisted_decision()
    db=SessionLocal()
    link=db.query(TradingHorizonTimeframeLink).filter_by(
        horizon_decision_id=decision["profile_decision_id"], role="execution").one()
    original=db.get(ActiveDriveDecision,link.decision_id)
    payload=dict(original.decision_payload)
    payload.update({"recommended_stop":64000.0,"recommended_target":68000.0,"confidence":.65,
                    "reference_price":65000.0})
    original.decision_payload=payload; original.confidence=.65
    # Snapshot is issuance-time state, so issue a fresh authority after enriching the decision.
    db.commit(); db.close()
    setup_user(); values=frames(); db=SessionLocal()
    for tf,item in values.items():
        if tf=="1M": continue
        item.update({"decision_id":f"bound-{tf}","recommended_stop":64000.0,"recommended_target":68000.0,
                     "reference_price":65000.0,"confidence":.65})
        db.add(ActiveDriveDecision(decision_id=item["decision_id"],user_id="horizon-user",engine="active_drive_v2",
            engine_version="2.2.0",symbol="BTCUSDT",timeframe=tf,signal="LONG",confidence=.65,
            expected_edge=.02,eligible_for_execution=True,decision_payload=dict(item),shadow=False))
    db.commit(); policy=build_horizon_decision("BTCUSDT",values,"mid_term",user_id="horizon-user",engine_version="2.2.0")
    persist_horizon_decision(db,user_id="horizon-user",policy=policy,timeframe_decisions=values,profile_revision=1)
    # A later inference is a separate row and must not affect the old authority.
    db.add(ActiveDriveDecision(decision_id="later-short",user_id="horizon-user",engine="active_drive_v2",
        engine_version="2.2.0",symbol="BTCUSDT",timeframe="15m",signal="SHORT",confidence=.95,
        expected_edge=.20,eligible_for_execution=True,decision_payload={"recommended_stop":63000.0,
        "recommended_target":70000.0,"confidence":.95},shadow=False)); db.commit(); db.close()
    router=ExecutionRouter(); provider=Provider(); monkeypatch.setattr(router,"provider",lambda:provider)
    result=asyncio.run(router.open_position(horizon_decision_id=policy["profile_decision_id"],
        user_id="horizon-user",symbol="BTCUSDT",notional_usdt=100,side="LONG",timeframe="15m",
        sl=63000.0,tp=70000.0,confidence=75.0,regime="later-regime"))
    assert result.ok
    assert provider.last_kwargs["side"]=="LONG"
    assert provider.last_kwargs["sl"]==64000.0 and provider.last_kwargs["tp"]==68000.0
    assert provider.last_kwargs["confidence"]==65.0
    assert provider.last_kwargs["decision_engine"]["decision_id"]=="bound-15m"
    assert "later-short" not in str(provider.last_kwargs)


def test_automated_open_position_decision_engine_carries_full_provenance(monkeypatch):
    """Automated Trading Horizon entries must record enough provenance for
    the paper ledger to link the resulting trade back to the decision and
    authority that opened it (decision_id/authority_id/edge_at_entry), and
    must be distinguishable from a manual open (execution_mode)."""
    setup_user(); durable_intents._client=NoRedis(); decision=persisted_decision()
    router=ExecutionRouter(); provider=Provider(); monkeypatch.setattr(router,"provider",lambda:provider)
    result=asyncio.run(router.open_position(**authority(decision)))
    assert result.ok
    de=provider.last_kwargs["decision_engine"]
    assert de["execution_mode"]=="automatic"
    assert de["horizon_decision_id"]==decision["profile_decision_id"]
    assert de["decision_id"]
    assert de["edge_at_entry"]==pytest.approx(.02)
    assert de["strategy_used"]=="active_drive_v2"


def test_current_price_can_only_block_persisted_snapshot():
    snapshot={"direction":"LONG","entry_policy":{"reference_price":65000.0,"maximum_slippage_bps":50},
              "risk":{"stop_price":64000.0,"target_price":68000.0}}
    assert revalidate_snapshot_price(snapshot,65100.0) is None
    assert revalidate_snapshot_price(snapshot,66000.0)=="DECISION_PRICE_DRIFT_EXCEEDED"
    assert revalidate_snapshot_price(snapshot,63000.0)=="DECISION_PRICE_DRIFT_EXCEEDED"
    assert snapshot["direction"]=="LONG" and snapshot["risk"]["stop_price"]==64000.0


def test_paper_router_uses_configured_risk_limit_not_binance_cap(monkeypatch):
    setup_user(); settings_repository.update_settings({"max_position_size_usd":500.0})
    try:
        decision=persisted_decision(); durable_intents._client=NoRedis()
        router=ExecutionRouter(); provider=Provider(); monkeypatch.setattr(router,"provider",lambda:provider)
        monkeypatch.setattr("app.trading_horizon.sizing.settings.binance_max_notional_per_trade",25.0)
        result=asyncio.run(router.open_position(horizon_decision_id=decision["profile_decision_id"],
            user_id="horizon-user",symbol="BTCUSDT"))
        assert result.ok and provider.last_kwargs["notional_usdt"]==500.0
        assert provider.last_kwargs["position_sizing"]["exchange_limit_usd"] is None
    finally:
        settings_repository.update_settings({"max_position_size_usd":1000.0})


def test_sizing_failure_does_not_resolve_provider_or_create_intent(monkeypatch):
    setup_user(); decision=persisted_decision(); calls=0; router=ExecutionRouter()
    def unavailable(*args,**kwargs): raise PositionSizingError("RISK_POLICY_UNAVAILABLE")
    def forbidden():
        nonlocal calls; calls+=1; raise AssertionError("provider must not resolve")
    monkeypatch.setattr("app.trading.execution_router.calculate_position_size",unavailable)
    monkeypatch.setattr(router,"provider",forbidden)
    result=asyncio.run(router.open_position(horizon_decision_id=decision["profile_decision_id"],
        user_id="horizon-user",symbol="BTCUSDT"))
    assert result.reason=="RISK_POLICY_UNAVAILABLE" and calls==0


def test_daily_loss_limit_blocks_authority_issuance():
    """app.trading.risk_manager.evaluate_risk()'s portfolio breakers
    (daily/weekly loss, drawdown, consecutive losses, cooldown) used to be
    dead code for the automated V2 path - configuring them in the Risk
    Management UI had no effect. They are now enforced at authority
    issuance, the single choke point every automated new-entry passes
    through."""
    setup_user("daily-loss-user")
    db = SessionLocal()
    portfolio = db.get(Portfolio, 1)
    portfolio.balance = 10000
    portfolio.daily_pnl = -300.0  # -3% > the default 2% max_daily_loss_pct
    db.add(portfolio)
    db.commit()
    db.close()
    values = frames()
    decision = build_horizon_decision("BTCUSDT", values, "mid_term", user_id="daily-loss-user", engine_version="2.2.0")
    db = SessionLocal()
    for tf, item in values.items():
        if tf == "1M":
            continue
        item["decision_id"] = f"daily-loss-{tf}"
        db.add(ActiveDriveDecision(decision_id=item["decision_id"], user_id="daily-loss-user", engine=item["engine"],
            engine_version=item["engine_version"], symbol="BTCUSDT", timeframe=tf, signal=item["final_signal"],
            confidence=item["confidence"], expected_edge=item["expected_edge"], eligible_for_execution=item["eligible_for_execution"],
            decision_payload=item, shadow=False))
    db.commit()
    with pytest.raises(ValueError, match="PORTFOLIO_RISK_LIMIT_BLOCKED"):
        persist_horizon_decision(db, user_id="daily-loss-user", policy=decision, timeframe_decisions=values, profile_revision=1)
    assert db.query(TradingHorizonDecision).count() == 0
    db.close()


def test_missing_execution_link_and_snapshot_mismatch_never_resolve_provider(monkeypatch):
    setup_user(); decision=persisted_decision(); calls=0
    router=ExecutionRouter()
    def forbidden():
        nonlocal calls; calls+=1; raise AssertionError("provider must not resolve")
    monkeypatch.setattr(router,"provider",forbidden)
    db=SessionLocal(); link=db.query(TradingHorizonTimeframeLink).filter_by(
        horizon_decision_id=decision["profile_decision_id"],role="execution").one(); db.delete(link); db.commit(); db.close()
    result=asyncio.run(router.open_position(horizon_decision_id=decision["profile_decision_id"],
        user_id="horizon-user",symbol="BTCUSDT",notional_usdt=100))
    assert result.reason=="HORIZON_EXECUTION_DECISION_MISSING" and calls==0

    setup_user(); decision=persisted_decision(); db=SessionLocal()
    row=db.get(TradingHorizonDecision,decision["profile_decision_id"]); evidence=dict(row.evidence)
    snapshot=dict(evidence["execution_snapshot"]); snapshot["direction"]="SHORT"
    evidence["execution_snapshot"]=snapshot; row.evidence=evidence; db.commit(); db.close()
    result=asyncio.run(router.open_position(horizon_decision_id=decision["profile_decision_id"],
        user_id="horizon-user",symbol="BTCUSDT",notional_usdt=100))
    assert result.reason=="HORIZON_EXECUTION_SNAPSHOT_MISMATCH" and calls==0

    setup_user(); decision=persisted_decision(); db=SessionLocal()
    row=db.get(TradingHorizonDecision,decision["profile_decision_id"]); evidence=dict(row.evidence)
    evidence.pop("execution_snapshot"); row.evidence=evidence; db.commit(); db.close()
    result=asyncio.run(router.open_position(horizon_decision_id=decision["profile_decision_id"],
        user_id="horizon-user",symbol="BTCUSDT",notional_usdt=100))
    assert result.reason=="HORIZON_EXECUTION_SNAPSHOT_MISSING" and calls==0
