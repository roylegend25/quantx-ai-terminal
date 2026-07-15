from app.api.analysis import prediction_resolution_summary, source_health
from app.api.dashboard import live_decision
from app.db.session import SessionLocal
from app.decision_engine.ledger import persist
from app.decision_engine.v2 import ActiveDriveV2Engine
from app.decision_engine.resolver import resolve_due


def legacy():
 return {"direction":"LONG","confidence":70,"strategies":{"trend":{"direction":"LONG","confidence":70,"reason":"trigger"},"momentum":{"direction":"NO_TRADE","confidence":0,"reason":"Confirmation missing"},"mean_reversion":{"direction":"NO_TRADE","confidence":0,"reason":"Volatility too high"},"breakout":{"direction":"NO_TRADE","confidence":0,"reason":"No breakout trigger"}},"ml_champion":{"used":False},"features":{"price":100,"ema20":101,"ema50":100,"ema200":99,"rsi":50,"macd_hist":.1,"atr":2,"bb_width":.01,"volume":10,"volume_sma20":10},"target":104,"stop":98,"data_quality":{"last_candle_time":123}}

def test_metrics_reconcile_and_only_failing_gates_block():
 db=SessionLocal()
 try:
  result=ActiveDriveV2Engine().evaluate({"db":db,"symbol":"DIAGUSDT","timeframe":"15m","legacy":legacy(),"regime":"TRENDING","data_status":"live","risk_reward_ratio":2})
  evidence=result["decision_metrics"]["evidence"]; margin=result["decision_metrics"]["point_margin"]
  assert evidence["value"]==round(sum(abs(x["absolute_points"]) for x in evidence["contributions"]),3)
  assert margin["value"]==round(abs(result["long_points"]-result["short_points"]),3)
  assert ("Insufficient total evidence" in result["blocking_reasons"]) is (not evidence["passed"])
  assert ("Point margin below required threshold" in result["blocking_reasons"]) is (not margin["passed"])
  assert result["confidence_diagnostics"]["failure_code"]
  assert result["confidence_diagnostics"]["failure_reason"]
  assert result["probability_up"] is None and result["confidence_diagnostics"]["indicative_point_score_up"] is not None
  assert result["recommended_target"] is None and result["recommended_stop"] is None
 finally:db.close()

def test_every_strategy_has_explicit_eligibility_and_rejection_reason():
 db=SessionLocal()
 try:
  result=ActiveDriveV2Engine().evaluate({"db":db,"symbol":"ELIGUSDT","timeframe":"15m","legacy":legacy(),"regime":"TRENDING","data_status":"live","risk_reward_ratio":2})
  strategies=[c for c in result["candidates"] if c["source_type"]=="strategy"]
  assert len(strategies)==14
  assert all("eligible_now" in c for c in strategies)
  assert all(c["eligible_now"] or (c["rejection_code"] and c["rejection_reason"]) for c in strategies)
 finally:db.close()

def test_source_health_is_bound_to_decision_and_quant_missing_inputs_are_explicit():
 db=SessionLocal()
 try:
  result=ActiveDriveV2Engine().evaluate({"db":db,"symbol":"BOUNDUSDT","timeframe":"15m","legacy":legacy(),"regime":"TRENDING","data_status":"live","risk_reward_ratio":2})
  decision_id=persist(db,"bound-user",result,100,legacy()["features"])
 finally:db.close()
 health=source_health("BOUNDUSDT","15m",decision_id)
 assert health["decision_snapshot"]["decision_id"]==decision_id
 unavailable=[s for s in health["sources"] if s["source_type"]=="quant" and s["runtime_status"]=="unavailable_data"]
 assert unavailable and all(s["missing_inputs"] and s["unavailable_code"] and s["unavailable_reason"] for s in unavailable)

def test_resolution_breakdowns_reconcile_global_total():
 summary=prediction_resolution_summary()
 assert summary["resolved"]==sum(x["resolved"] for x in summary["by_timeframe"])
 assert summary["total_predictions"]==sum(x["total_predictions"] for x in summary["by_timeframe"])
 assert {"1m","3m","5m","15m","30m","1h","4h","1d","unknown/legacy"} <= {x["key"] for x in summary["by_timeframe"]}

def test_resolver_skips_legacy_gaps_without_starving_later_records():
 from datetime import datetime,timedelta,timezone
 from app.db.models import MarketCandle,PredictionLedger,PredictionResolution
 db=SessionLocal()
 try:
  now=datetime.now(timezone.utc)
  for index in range(2):
   generated=now-timedelta(minutes=20-index)
   db.add(PredictionLedger(prediction_id=f"resolver-gap-{index}",candidate_id=f"candidate-gap-{index}",decision_id="decision-gap",user_id="admin",engine="active_drive_v2",engine_version="2.2.0",source_type="strategy",source_name="gap_test",source_version="1",symbol=("NOCANDLE" if index==0 else "GAPUSDT"),timeframe="5m",direction="LONG",confidence=0.5,target_horizon_seconds=300,feature_snapshot_hash=f"hash-{index}",generated_at=generated,resolution_deadline=generated+timedelta(minutes=5),reference_price=100.0))
  db.flush()
  later=db.query(PredictionLedger).filter(PredictionLedger.prediction_id=="resolver-gap-1").one()
  db.add(MarketCandle(symbol="GAPUSDT",timeframe="5m",timestamp=int(later.resolution_deadline.timestamp()*1000),open=100,high=102,low=99,close=101,volume=1))
  db.commit()
  assert resolve_due(db,limit=1,scan_limit=10)==1
  assert db.query(PredictionResolution).filter(PredictionResolution.prediction_id=="resolver-gap-1").count()==1
  assert db.query(PredictionResolution).filter(PredictionResolution.prediction_id=="resolver-gap-0").count()==0
 finally:db.close()

def test_live_decision_is_scoped_bounded_and_has_normalized_requirements():
 db=SessionLocal()
 try:
  result=ActiveDriveV2Engine().evaluate({"db":db,"symbol":"LIVEUSDT","timeframe":"15m","legacy":legacy(),"regime":"TRENDING","data_status":"live","risk_reward_ratio":2})
  decision_id=persist(db,"live-user",result,100,legacy()["features"])
  body=live_decision("LIVEUSDT","15m","live-user",db)
  assert body["decision_id"]==decision_id
  assert body["engine"]["id"]=="active_drive_v2"
  assert len(body["decision_history"])<=120
  assert body["signal"]=="NO_TRADE"
  assert body["execution_eligible"] is False
  assert all({"id","status","formula","scope","explanation"}<=set(item) for item in body["requirements"])
  assert any(item["id"]=="point_margin" for item in body["requirements"])
 finally:db.close()
