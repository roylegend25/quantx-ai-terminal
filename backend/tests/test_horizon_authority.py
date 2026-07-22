"""DEPRECATED (historical-only module coverage): app.trading_horizon.authority
is no longer part of the production execution path (see
app.decision_engine.execution_gate, which now performs authority issuance/
validation directly against ActiveDriveDecision - the single-authoritative-
decision replacement for Trading Horizon). These tests exercise
authority.py's own issuance/idempotency logic in isolation only, to prove
the historical module itself still behaves correctly for any code that
might still reference it; execution_router.open_position() integration
tests for the current architecture live in test_execution_router_v2_decisions.py."""
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


def test_current_price_can_only_block_persisted_snapshot():
    snapshot={"direction":"LONG","entry_policy":{"reference_price":65000.0,"maximum_slippage_bps":50},
              "risk":{"stop_price":64000.0,"target_price":68000.0}}
    assert revalidate_snapshot_price(snapshot,65100.0) is None
    assert revalidate_snapshot_price(snapshot,66000.0)=="DECISION_PRICE_DRIFT_EXCEEDED"
    assert revalidate_snapshot_price(snapshot,63000.0)=="DECISION_PRICE_DRIFT_EXCEEDED"
    assert snapshot["direction"]=="LONG" and snapshot["risk"]["stop_price"]==64000.0


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

