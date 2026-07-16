import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from sqlalchemy.exc import OperationalError

from app.api import timeframes as module
from app.db.models import (PredictionLedger, PredictionResolution, TradingHorizonConsumption,
                           TradingHorizonDecision, TradingHorizonTimeframeLink, UserBotSetting)
from app.db.session import SessionLocal
from app.trading_horizon.service import PROFILES


def set_engine(user,engine):
    db=SessionLocal(); row=db.get(UserBotSetting,user) or UserBotSetting(user_id=user)
    row.decision_engine=engine; row.trading_profile="mid_term"; row.profile_revision=1
    db.add(row); db.commit(); db.close()


def counts():
    db=SessionLocal(); result=(db.query(TradingHorizonDecision).count(),db.query(TradingHorizonTimeframeLink).count()); db.close(); return result


def test_v1_returns_blocked_preview_without_persistence():
    set_engine("preview-v1","active_drive_v1"); before=counts()
    response=asyncio.run(module.trading_horizon("BTCUSDT",current_user="preview-v1"))
    assert response["authority_status"]=="preview_blocked" and response["persisted"] is False
    assert response["horizon_decision_id"] is None and response["execution_eligible"] is False
    assert response["blocking_reasons"][0]["code"]=="ACTIVE_DRIVE_V2_NOT_AUTHORITATIVE"
    assert counts()==before


def test_no_active_engine_returns_blocked_preview(monkeypatch):
    setting=SimpleNamespace(decision_engine=None,trading_profile="mid_term",profile_revision=1,
                            strict_timeframe_unanimity=True)
    monkeypatch.setattr(module,"get_setting",lambda db,user:setting)
    response=asyncio.run(module.trading_horizon("BTCUSDT",current_user="preview-none"))
    assert response["authority_status"]=="preview_blocked"
    assert response["selected_engine"] is None and response["persisted"] is False


def test_v2_missing_durable_decision_is_preview_not_partial_persistence(monkeypatch):
    set_engine("preview-missing","active_drive_v2"); before=counts()
    async def incomplete(symbol,timeframe,current_user):
        return {"prediction":{"decision_engine":{"symbol":symbol,"timeframe":timeframe,"engine":"active_drive_v2",
            "engine_version":"2.2.0","final_signal":"LONG","direction":"LONG","eligible_for_execution":True,
            "confidence":.8,"expected_edge":.02,"current_edge_supported":True}}}
    monkeypatch.setattr("app.api.prediction.prediction",incomplete)
    response=asyncio.run(module.trading_horizon("BTCUSDT",current_user="preview-missing"))
    assert response["persisted"] is False
    assert response["blocking_reasons"][0]["code"]=="HORIZON_REQUIRED_DECISION_NOT_DURABLE"
    assert counts()==before


def test_ready_public_preview_never_calls_issuance(monkeypatch):
    set_engine("preview-failure","active_drive_v2"); before=counts()
    async def complete_shape(symbol,timeframe,current_user):
        return {"prediction":{"decision_engine":{"decision_id":f"id-{timeframe}","symbol":symbol,"timeframe":timeframe,
            "engine":"active_drive_v2","engine_version":"2.2.0","final_signal":"LONG","direction":"LONG",
            "eligible_for_execution":True,"confidence":.8,"expected_edge":.02,"current_edge_supported":True}}}
    monkeypatch.setattr("app.api.prediction.prediction",complete_shape)
    monkeypatch.setattr(module,"issue_horizon_authority",lambda *args,**kwargs:(_ for _ in ()).throw(
        AssertionError("GET must not issue authority")))
    response=asyncio.run(module.trading_horizon("BTCUSDT",current_user="preview-failure"))
    assert response["authority_status"]=="preview_ready"
    assert response["preview_execution_eligible"] is True and response["execution_eligible"] is False
    assert response["persisted"] is False and response["horizon_decision_id"] is None
    assert counts()==before


def test_one_hundred_get_previews_create_no_authority_side_effects(monkeypatch):
    set_engine("preview-repeat","active_drive_v2"); before=counts()
    db=SessionLocal(); consumptions_before=db.query(TradingHorizonConsumption).count(); db.close()
    async def complete_shape(symbol,timeframe,current_user):
        return {"prediction":{"decision_engine":{"decision_id":f"repeat-{timeframe}","symbol":symbol,
            "timeframe":timeframe,"engine":"active_drive_v2","engine_version":"2.2.0",
            "final_signal":"LONG","direction":"LONG","eligible_for_execution":True,
            "confidence":.8,"expected_edge":.02,"current_edge_supported":True}}}
    monkeypatch.setattr("app.api.prediction.prediction",complete_shape)
    for _ in range(100):
        response=asyncio.run(module.trading_horizon("BTCUSDT",current_user="preview-repeat"))
        assert response["authority_status"]=="preview_ready"
        assert response["execution_eligible"] is False
        assert response["persisted"] is False and response["horizon_decision_id"] is None
    assert counts()==before
    db=SessionLocal(); assert db.query(TradingHorizonConsumption).count()==consumptions_before; db.close()


def test_historical_edge_from_another_user_is_excluded():
    set_engine("history-owner-b","active_drive_v1"); db=SessionLocal(); now=datetime.now(timezone.utc)
    db.add(PredictionLedger(prediction_id="history-owner-a-p",candidate_id="history-owner-a-c",decision_id="history-owner-a-d",
        user_id="history-owner-a",engine="active_drive_v2",engine_version="2.2.0",source_type="strategy",
        source_name="trend",source_version="2.1.0",symbol="BTCUSDT",timeframe="15m",direction="LONG",
        confidence=.8,points=5,expected_edge=.80,target_horizon_seconds=900,resolution_deadline=now+timedelta(minutes=15),
        feature_snapshot_hash="hash",generated_at=now))
    db.add(PredictionResolution(prediction_id="history-owner-a-p",actual_return=-.0025,resolved_direction="LONG",
        correct=False,neutral_result=False,resolution_reason="resolved",resolved_at=now+timedelta(seconds=1))); db.commit(); db.close()
    response=asyncio.run(module.trading_horizon("BTCUSDT",current_user="history-owner-b"))
    summary=response["historical_edge_summary"]
    assert summary["historical_expected_edge"] is None and summary["historical_edge_sample_count"]==0


def test_ledger_failure_preserves_required_state_and_returns_degraded_preview(monkeypatch):
    set_engine("degraded-mid","active_drive_v2")
    async def incomplete(symbol,timeframe,current_user):
        return {"prediction":{"decision_engine":{"symbol":symbol,"timeframe":timeframe,"engine":"active_drive_v2",
            "engine_version":"2.2.0","final_signal":"NO_TRADE","direction":"NO_TRADE",
            "eligible_for_execution":False,"confidence":None,"expected_edge":None,"current_edge_supported":False}}}
    monkeypatch.setattr("app.api.prediction.prediction",incomplete)
    monkeypatch.setattr(module,"_load_historical_performance",lambda *args,**kwargs:(_ for _ in ()).throw(
        OperationalError("SELECT",{},RuntimeError("missing table"))))
    response=asyncio.run(module.trading_horizon("BTCUSDT",chart_timeframe="1M",current_user="degraded-mid"))
    assert response["degraded"] is True and response["historical_data_available"] is False
    assert response["historical_data_error_code"]=="HISTORICAL_LEDGER_UNAVAILABLE"
    assert response["active_engine"]=="active_drive_v2" and response["required_engine"]=="active_drive_v2"
    assert response["requested_profile"]=="mid_term" and response["profile_revision"]==1
    assert response["persisted"] is False and response["execution_eligible"] is False
    assert response["authority_status"]=="preview_blocked" and response["horizon_decision_id"] is None


def test_degraded_history_blocks_even_complete_durable_shapes(monkeypatch):
    set_engine("degraded-complete","active_drive_v2"); persistence_calls={"count":0}
    async def complete(symbol,timeframe,current_user):
        return {"prediction":{"decision_engine":{"decision_id":f"complete-{timeframe}","symbol":symbol,
            "timeframe":timeframe,"engine":"active_drive_v2","engine_version":"2.2.0","final_signal":"LONG",
            "direction":"LONG","eligible_for_execution":True,"confidence":.8,"expected_edge":.02,
            "current_edge_supported":True}}}
    monkeypatch.setattr("app.api.prediction.prediction",complete)
    monkeypatch.setattr(module,"_load_historical_performance",lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError("db")))
    monkeypatch.setattr(module,"issue_horizon_authority",lambda *args,**kwargs:persistence_calls.__setitem__("count",1))
    response=asyncio.run(module.trading_horizon("BTCUSDT",current_user="degraded-complete"))
    assert response["blocking_reasons"][0]["code"]=="HISTORICAL_LEDGER_UNAVAILABLE"
    assert response["persisted"] is False and persistence_calls["count"]==0


def test_inactive_v2_plus_ledger_failure_keeps_authoritative_blocker(monkeypatch):
    set_engine("degraded-v1","active_drive_v1")
    monkeypatch.setattr(module,"_load_historical_performance",lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError("db")))
    response=asyncio.run(module.trading_horizon("BTCUSDT",current_user="degraded-v1"))
    assert response["degraded"] is True
    assert response["blocking_reasons"][0]["code"]=="ACTIVE_DRIVE_V2_NOT_AUTHORITATIVE"
    assert response["persisted"] is False


def test_empty_and_available_ledgers_have_explicit_metadata():
    set_engine("empty-ledger","active_drive_v1")
    empty=asyncio.run(module.trading_horizon("BTCUSDT",current_user="empty-ledger"))
    assert empty["historical_data_available"] is True and empty["previous_resolved_count"]==0
    assert empty["historical_edge_summary"]["historical_expected_edge"] is None
    set_engine("history-owner-a","active_drive_v1")
    available=asyncio.run(module.trading_horizon("BTCUSDT",current_user="history-owner-a"))
    assert available["historical_data_available"] is True
    summary=available["historical_edge_summary"]
    assert summary["historical_expected_edge"]==-.0025
    assert summary["historical_performance"]["realized_return_mean"]==-.0025
    assert summary["forecast_calibration"]["forecast_expected_edge_mean"]==.80


def test_realized_and_forecast_statistics_remain_distinct_and_preserve_flat_returns():
    summary=module._historical_metrics({
        "realized_returns":[-.01,0.0,.005],
        "forecast_expected_edges":[.01,.02,.03],
        "forecast_confidences":[.6,.7,.8],
    },owner_id="stats-user",symbol="BTCUSDT",timeframe="15m")
    realized=summary["historical_performance"]
    forecast=summary["forecast_calibration"]
    assert realized["resolved_sample_count"]==3
    assert realized["realized_return_mean"]==pytest.approx(-.005/3)
    assert realized["realized_return_median"]==0.0
    assert realized["positive_count"]==1 and realized["negative_count"]==1 and realized["flat_count"]==1
    assert realized["realized_edge_lower_bound"]==-.01 and realized["realized_edge_upper_bound"]==.005
    assert forecast["forecast_expected_edge_mean"]==pytest.approx(.02)
    assert summary["historical_expected_edge"] != forecast["forecast_expected_edge_mean"]


def test_missing_realized_return_never_falls_back_to_forecast_and_bounds_require_samples():
    summary=module._historical_metrics({
        "realized_returns":[], "forecast_expected_edges":[.008], "forecast_confidences":[.9],
    },owner_id="missing-return",symbol="BTCUSDT",timeframe="15m")
    realized=summary["historical_performance"]
    assert realized["resolved_sample_count"]==0 and realized["realized_return_mean"] is None
    assert realized["supported"] is False and realized["reason"]=="INSUFFICIENT_RESOLVED_SAMPLES"
    assert realized["realized_edge_lower_bound"] is None and realized["realized_edge_upper_bound"] is None
    assert summary["historical_expected_edge"] is None
    assert summary["forecast_calibration"]["forecast_expected_edge_mean"]==.008


def test_explicit_profiles_survive_history_failure(monkeypatch):
    monkeypatch.setattr(module,"_load_historical_performance",lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError("db")))
    for profile in ("short_term","mid_term","safe"):
        user=f"degraded-{profile}"; set_engine(user,"active_drive_v1")
        db=SessionLocal(); setting=db.get(UserBotSetting,user); setting.trading_profile=profile; db.commit(); db.close()
        response=asyncio.run(module.trading_horizon("BTCUSDT",current_user=user))
        assert response["requested_profile"]==profile and response["resolved_profile"]==profile
        assert response["degraded"] is True and response["persisted"] is False


def _set_auto_engine(user):
    set_engine(user,"active_drive_v2")
    db=SessionLocal(); setting=db.get(UserBotSetting,user); setting.trading_profile="auto_adaptive"
    setting.profile_revision=1; db.commit(); db.close()


def test_internal_auto_evaluation_calls_each_timeframe_once_and_issues_same_results(monkeypatch):
    user="single-evaluation"; _set_auto_engine(user); calls={}; captured={}
    async def prediction(symbol,timeframe,current_user):
        calls[timeframe]=calls.get(timeframe,0)+1
        return {"prediction":{"decision_engine":{"decision_id":f"once-{timeframe}","symbol":symbol,
            "timeframe":timeframe,"engine":"active_drive_v2","engine_version":"2.2.0",
            "final_signal":"LONG","direction":"LONG","eligible_for_execution":True,"confidence":.8,
            "expected_edge":.03,"current_edge_supported":True,"recommended_stop":64000.0,
            "recommended_target":68000.0,"generated_at":datetime.now(timezone.utc).isoformat(),
            "expires_at":(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat()}}}
    def issue(db,**kwargs):
        captured.update(kwargs)
        execution_tf=kwargs["policy"]["execution_timeframe"]
        decision_id=kwargs["timeframe_decisions"][execution_tf]["decision_id"]
        return SimpleNamespace(id="authority-once",evidence={"execution_snapshot":{
            "authoritative_decision_id":decision_id,"execution_timeframe":execution_tf,
            "risk":{},"holding":{},"risk_approval":{}}})
    monkeypatch.setattr("app.api.prediction.prediction",prediction)
    monkeypatch.setattr(module,"issue_horizon_authority",issue)
    result=asyncio.run(module.evaluate_and_issue_horizon_authority(user_id=user,account_id="default",
        symbol="BTCUSDT",evaluation_reason="scheduler",idempotency_key="cycle-1"))
    expected={tf for profile in PROFILES.values() for tf in (*profile.required_timeframes,profile.structural_bias_timeframe)}
    assert set(calls)==expected and all(count==1 for count in calls.values())
    assert result["prediction_calls"]==len(expected) and result["authority_status"]=="persisted_authority"
    assert result["authoritative_execution_decision_id"] == captured["timeframe_decisions"][result["execution_timeframe"]]["decision_id"]
    assert captured["timeframe_decisions"][result["execution_timeframe"]]["recommended_stop"]==64000.0
    assert captured["timeframe_decisions"][result["execution_timeframe"]]["recommended_target"]==68000.0
    assert {item["evaluation_id"] for item in captured["timeframe_decisions"].values()}=={result["evaluation_id"]}


def test_timeframe_evaluation_respects_concurrency_bound(monkeypatch):
    user="bounded-evaluation"; _set_auto_engine(user); active=0; maximum=0
    async def prediction(symbol,timeframe,current_user):
        nonlocal active,maximum
        active+=1; maximum=max(maximum,active)
        await asyncio.sleep(.01)
        active-=1
        return {"prediction":{"decision_engine":{"decision_id":f"bounded-{timeframe}","symbol":symbol,
            "timeframe":timeframe,"engine":"active_drive_v2","engine_version":"2.2.0",
            "final_signal":"NO_TRADE","direction":"NO_TRADE","eligible_for_execution":False,
            "confidence":None,"expected_edge":None,"current_edge_supported":False}}}
    monkeypatch.setattr("app.api.prediction.prediction",prediction)
    monkeypatch.setattr(module.settings,"horizon_evaluation_concurrency",2)
    result=asyncio.run(module._evaluate_horizon("BTCUSDT",current_user=user,issue_authority=False))
    assert maximum==2 and result["prediction_calls"]==result["timeframe_count"]
    assert result["authority_status"]=="preview_blocked"


def test_concurrent_scheduler_requests_share_one_in_process_evaluation(monkeypatch):
    user="shared-evaluation"; set_engine(user,"active_drive_v2"); calls={}
    async def prediction(symbol,timeframe,current_user):
        calls[timeframe]=calls.get(timeframe,0)+1
        await asyncio.sleep(.01)
        return {"prediction":{"decision_engine":{"decision_id":f"shared-{timeframe}","symbol":symbol,
            "timeframe":timeframe,"engine":"active_drive_v2","engine_version":"2.2.0",
            "final_signal":"NO_TRADE","direction":"NO_TRADE","eligible_for_execution":False,
            "confidence":None,"expected_edge":None,"current_edge_supported":False}}}
    monkeypatch.setattr("app.api.prediction.prediction",prediction)
    async def run_two():
        kwargs=dict(user_id=user,account_id="default",symbol="BTCUSDT",
                    evaluation_reason="scheduler",idempotency_key="same-cycle")
        return await asyncio.gather(module.evaluate_and_issue_horizon_authority(**kwargs),
                                    module.evaluate_and_issue_horizon_authority(**kwargs))
    first,second=asyncio.run(run_two())
    assert first["evaluation_id"]==second["evaluation_id"]
    assert all(count==1 for count in calls.values())


def test_timeframe_timeout_blocks_without_issuance(monkeypatch):
    user="evaluation-timeout"; set_engine(user,"active_drive_v2"); issued={"count":0}
    async def prediction(symbol,timeframe,current_user):
        if timeframe=="1h": await asyncio.sleep(.05)
        return {"prediction":{"decision_engine":{"decision_id":f"timeout-{timeframe}","symbol":symbol,
            "timeframe":timeframe,"engine":"active_drive_v2","engine_version":"2.2.0",
            "final_signal":"LONG","direction":"LONG","eligible_for_execution":True,"confidence":.8,
            "expected_edge":.03,"current_edge_supported":True}}}
    monkeypatch.setattr("app.api.prediction.prediction",prediction)
    monkeypatch.setattr(module.settings,"horizon_timeframe_timeout_seconds",.01)
    monkeypatch.setattr(module.settings,"horizon_evaluation_timeout_seconds",1)
    monkeypatch.setattr(module,"issue_horizon_authority",lambda *a,**k:issued.__setitem__("count",1))
    result=asyncio.run(module.evaluate_and_issue_horizon_authority(user_id=user,account_id="default",
        symbol="BTCUSDT",evaluation_reason="scheduler"))
    assert result["authority_status"]=="preview_blocked" and result["persisted"] is False
    assert result["blocking_reasons"][0]["code"]=="TIMEFRAME_EVALUATION_TIMEOUT"
    assert result["timeout_count"]==1 and issued["count"]==0


def test_overall_deadline_blocks_all_partial_authority(monkeypatch):
    user="evaluation-deadline"; set_engine(user,"active_drive_v2"); issued={"count":0}
    async def slow(*args,**kwargs):
        await asyncio.sleep(.05)
    monkeypatch.setattr("app.api.prediction.prediction",slow)
    monkeypatch.setattr(module.settings,"horizon_timeframe_timeout_seconds",1)
    monkeypatch.setattr(module.settings,"horizon_evaluation_timeout_seconds",.01)
    monkeypatch.setattr(module,"issue_horizon_authority",lambda *a,**k:issued.__setitem__("count",1))
    result=asyncio.run(module.evaluate_and_issue_horizon_authority(user_id=user,account_id="default",
        symbol="BTCUSDT",evaluation_reason="scheduler"))
    assert result["blocking_reasons"][0]["code"]=="HORIZON_EVALUATION_DEADLINE_EXCEEDED"
    assert result["persisted"] is False and issued["count"]==0
