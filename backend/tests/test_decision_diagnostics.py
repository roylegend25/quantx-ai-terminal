from app.api.analysis import prediction_resolution_summary, source_health
from app.db.session import SessionLocal
from app.decision_engine.ledger import persist
from app.decision_engine.v2 import ActiveDriveV2Engine


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
