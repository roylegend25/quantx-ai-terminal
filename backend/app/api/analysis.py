"""Authenticated, read-only Active Drive diagnostics from one persisted snapshot."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func
from app.core.config import settings
from app.db.models import ActiveDriveDecision, MarketCandle, PredictionLedger, PredictionResolution, SignalCandidateRecord
from app.db.session import SessionLocal
from app.decision_engine import scheduler as resolver_scheduler
from app.decision_engine.v2 import SHADOW_MODELS
from app.quant.forecast import TIMEFRAME_SECONDS

router=APIRouter(prefix="/api/analysis",tags=["analysis"])
QUANT_INPUTS={
 "regression_slope_proxy":["ema20","ema50"],"kalman_trend_proxy":["ema20","ema50"],"mean_reversion_zscore":["price","ema20","atr"],"atr_expected_move":["price","atr"],
 "realized_volatility":["realized_volatility"],"compression_state":["bb_width"],"volume_anomaly":["volume","volume_sma20"],"persistence_proxy":["rsi"],
 "funding_divergence":["price","funding_history"],"open_interest_divergence":["price","open_interest_history"],"order_book_imbalance":["order_book"],"correlation_beta_context":["btc_eth_history"]}
MISSING_CODES={"funding_divergence":"MISSING_FUNDING_HISTORY","open_interest_divergence":"MISSING_OPEN_INTEREST_HISTORY","order_book_imbalance":"MISSING_ORDER_BOOK","correlation_beta_context":"MISSING_CROSS_ASSET_HISTORY"}
TIMEFRAMES=["1m","3m","5m","15m","30m","1h","4h","1d","unknown/legacy"]

def _iso(v):return v.isoformat() if v else None

def _group(rows,key_fn):
    groups=defaultdict(list)
    for ledger,resolution in rows:groups[str(key_fn(ledger) or "unknown/legacy")].append((ledger,resolution))
    out=[]
    for key,items in sorted(groups.items()):
        resolved=[(l,r) for l,r in items if r is not None]; correct=sum(r.correct is True for _,r in resolved); wrong=sum(r.correct is False for _,r in resolved); neutral=sum(bool(r.neutral_result) for _,r in resolved); directional=correct+wrong; returns=[r.actual_return for _,r in resolved if r.actual_return is not None]
        out.append({"key":key,"total_predictions":len(items),"resolved":len(resolved),"unresolved":len(items)-len(resolved),"correct":correct,"wrong":wrong,"neutral":neutral,"accuracy":round(correct/directional,4) if directional>=20 else None,"average_realized_return":round(sum(returns)/len(returns),8) if returns else None,"expected_edge_sample_count":directional,"first_prediction":_iso(min((l.generated_at for l,_ in items),default=None)),"latest_prediction":_iso(max((l.generated_at for l,_ in items),default=None))})
    return out

def _filtered_rows(db,symbol,timeframe,engine,source_type,source_name,source_version,market_regime,date_from,date_to):
    q=db.query(PredictionLedger,PredictionResolution).outerjoin(PredictionResolution,PredictionResolution.prediction_id==PredictionLedger.prediction_id)
    for field,value in ((PredictionLedger.symbol,symbol.upper() if symbol else None),(PredictionLedger.timeframe,timeframe.lower() if timeframe else None),(PredictionLedger.engine,engine),(PredictionLedger.source_type,source_type),(PredictionLedger.source_name,source_name),(PredictionLedger.source_version,source_version),(PredictionLedger.market_regime,market_regime)):
        if value:q=q.filter(field==value)
    if date_from:q=q.filter(PredictionLedger.generated_at>=date_from)
    if date_to:q=q.filter(PredictionLedger.generated_at<=date_to)
    return q.all()

@router.get("/prediction-resolution-summary")
def prediction_resolution_summary(symbol:str|None=None,timeframe:str|None=None,engine:str|None=None,source_type:str|None=None,source_name:str|None=None,source_version:str|None=None,market_regime:str|None=None,date_from:datetime|None=None,date_to:datetime|None=None):
    db=SessionLocal()
    try:
        rows=_filtered_rows(db,symbol,timeframe,engine,source_type,source_name,source_version,market_regime,date_from,date_to); resolved=[(l,r) for l,r in rows if r is not None]; correct=sum(r.correct is True for _,r in resolved); wrong=sum(r.correct is False for _,r in resolved); neutral=sum(bool(r.neutral_result) for _,r in resolved); now=datetime.utcnow()
        expired=sum(r is None and l.resolution_deadline < now-timedelta(seconds=TIMEFRAME_SECONDS.get(l.timeframe,300)) for l,r in rows)
        by_tf=_group(rows,lambda l:l.timeframe); present={x["key"] for x in by_tf}; by_tf.extend({"key":tf,"total_predictions":0,"resolved":0,"unresolved":0,"correct":0,"wrong":0,"neutral":0,"accuracy":None,"average_realized_return":None,"expected_edge_sample_count":0,"first_prediction":None,"latest_prediction":None} for tf in TIMEFRAMES if tf not in present)
        return {"total_predictions":len(rows),"resolved":len(resolved),"unresolved":len(rows)-len(resolved),"expired_unresolved":expired,"correct":correct,"wrong":wrong,"neutral":neutral,
          "by_symbol":_group(rows,lambda l:l.symbol),"by_timeframe":sorted(by_tf,key=lambda x:TIMEFRAMES.index(x["key"]) if x["key"] in TIMEFRAMES else 99),"by_symbol_timeframe":_group(rows,lambda l:f"{l.symbol} {l.timeframe}"),"by_engine":_group(rows,lambda l:l.engine),"by_source_type":_group(rows,lambda l:l.source_type),"by_source":_group(rows,lambda l:f"{l.source_type}:{l.source_name}:{l.source_version}"),"by_regime":_group(rows,lambda l:l.market_regime)}
    finally:db.close()

@router.get("/source-health")
def source_health(symbol:str=Query("BTCUSDT"),timeframe:str=Query("15m"),decision_id:str|None=None):
    symbol,timeframe=symbol.upper(),timeframe.lower(); db=SessionLocal()
    try:
        q=db.query(ActiveDriveDecision).filter(ActiveDriveDecision.engine=="active_drive_v2",ActiveDriveDecision.symbol==symbol,ActiveDriveDecision.timeframe==timeframe)
        decision=db.get(ActiveDriveDecision,decision_id) if decision_id else q.order_by(ActiveDriveDecision.created_at.desc()).first()
        if decision is None:raise HTTPException(404,"Decision snapshot not found")
        if decision.symbol!=symbol or decision.timeframe!=timeframe or decision.engine!="active_drive_v2":raise HTTPException(409,"Decision snapshot does not match selected engine/symbol/timeframe")
        rows=db.query(SignalCandidateRecord).filter(SignalCandidateRecord.decision_id==decision.decision_id).all(); sources=[]
        for row in rows:
            evidence=row.evidence or {}; diag=evidence.get("diagnostics") or {}; shadow=row.source_type=="ml" and row.source_version=="shadow-1"; current=evidence.get("current_value")
            missing=bool(row.source_name in MISSING_CODES and current is None); runtime="shadow_not_inferred" if shadow else "unavailable_data" if missing else "working"; eligible=bool(diag.get("eligible_now",row.eligible) and not shadow)
            item={"source_type":row.source_type,"source_name":row.source_name,"name":row.source_name,"version":row.source_version,"family":row.source_family,"configured_status":"shadow" if shadow else "enabled","runtime_status":runtime,"dependency_available":not missing,"production_eligible":bool(row.eligible and not shadow),"shadow":shadow,"last_successfully_evaluated_time":_iso(row.created_at),"last_error":row.rejection_reason,"reason":evidence.get("reason") or evidence.get("explanation") or row.rejection_reason,"supported_symbols":[symbol],"supported_timeframes":[timeframe],"supported_regimes":[row.market_regime] if row.market_regime else [],"direction":row.direction,"final_points":row.candidate_points,"points":row.candidate_points,"resolved_samples":row.resolved_sample_size,"historical_evidence_tier":row.evidence_tier,"evidence_tier":row.evidence_tier,"fresh":row.data_freshness=="live","eligible_now":eligible,"regime_compatible":diag.get("regime_compatible",True),"required_data_available":diag.get("required_data_available",not missing),"rejection_code":diag.get("rejection_code") if not eligible else None,"rejection_reason":row.rejection_reason if not eligible else None,
              "raw_confidence":diag.get("raw_confidence",row.confidence),"calibrated_confidence":diag.get("calibrated_confidence"),"base_points":diag.get("base_points"),"reliability_weight":diag.get("reliability_weight"),"sample_size_weight":diag.get("sample_size_weight"),"symbol_weight":diag.get("symbol_weight"),"timeframe_weight":diag.get("timeframe_weight"),"regime_weight":diag.get("regime_weight"),"recent_performance_weight":diag.get("recent_performance_weight"),"calibration_weight":diag.get("calibration_weight"),"correlation_penalty":diag.get("correlation_penalty")}
            if row.source_type=="quant":
                req=QUANT_INPUTS.get(row.source_name,[]); item.update({"required_inputs":req,"missing_inputs":req[1:] if missing and len(req)>1 else req if missing else [],"normalized_score":evidence.get("normalized_score"),"current_value":current,"unavailable_code":MISSING_CODES.get(row.source_name) if missing else None,"unavailable_reason":row.rejection_reason if missing else None})
            sources.append(item)
        known={s["source_name"] for s in sources}
        for name,family in SHADOW_MODELS:
            if name not in known:sources.append({"source_type":"ml","source_name":name,"name":name,"version":"shadow-1","family":family,"configured_status":"shadow","runtime_status":"shadow_not_inferred","dependency_available":False,"production_eligible":False,"shadow":True,"eligible_now":False,"direction":"NO_TRADE","final_points":0,"rejection_code":"SHADOW_ONLY","rejection_reason":"No validated artifact/inference wired into V2","resolved_samples":0,"fresh":False})
        now=datetime.now(timezone.utc); base=db.query(PredictionLedger).filter(PredictionLedger.symbol==symbol,PredictionLedger.timeframe==timeframe); total=base.count(); resolved=base.join(PredictionResolution,PredictionResolution.prediction_id==PredictionLedger.prediction_id).count(); grace=timedelta(seconds=TIMEFRAME_SECONDS.get(timeframe,300)); expired=base.outerjoin(PredictionResolution,PredictionResolution.prediction_id==PredictionLedger.prediction_id).filter(PredictionResolution.id.is_(None),PredictionLedger.resolution_deadline<now-grace).count(); candle_max=db.query(func.max(MarketCandle.timestamp)).filter(MarketCandle.symbol==symbol,MarketCandle.timeframe==timeframe).scalar(); resolver=resolver_scheduler.status(); resolver.update({"healthy":bool(resolver.get("running") and not resolver.get("last_error") and expired==0),"expired_unresolved":expired,"market_candle_latest":candle_max,"degraded_reason":"Expired predictions lack stored outcome candles" if expired else None})
        types=Counter(s["source_type"] for s in sources); working=Counter(s["source_type"] for s in sources if s["runtime_status"]=="working"); payload=decision.decision_payload or {}; metrics=payload.get("decision_metrics") or {}; history=payload.get("history") or {}
        return {"decision_snapshot":{"decision_id":decision.decision_id,"symbol":decision.symbol,"timeframe":decision.timeframe,"engine":decision.engine,"engine_version":decision.engine_version,"generated_at":payload.get("generated_at") or _iso(decision.created_at),"market_data_revision":payload.get("market_data_revision"),"performance_snapshot_revision":payload.get("performance_snapshot_revision")},
          "summary":{"ml_total":types["ml"],"ml_working":working["ml"],"strategy_total":types["strategy"],"strategy_working":working["strategy"],"strategy_eligible_now":sum(s["source_type"]=="strategy" and s.get("eligible_now") for s in sources),"quant_total":types["quant"],"quant_working":working["quant"],"quant_unavailable":sum(s["source_type"]=="quant" and s["runtime_status"]=="unavailable_data" for s in sources),"candidates_generated":len(rows),"ledger_writes":total,"resolver_healthy":resolver["healthy"]},"sources":sorted(sources,key=lambda x:(x["source_type"],x["source_name"])),"ledger":{"total":total,"resolved":resolved,"unresolved":total-resolved,"expired_unresolved":expired},"resolver":resolver,
          "decision_requirements":{"decision_id":decision.decision_id,"signal":decision.signal,"metrics":metrics,"history":history,"blocking_reasons":decision.blocking_reasons,"long_points":decision.long_points,"short_points":decision.short_points,"confidence_diagnostics":payload.get("confidence_diagnostics"),"expected_edge":decision.expected_edge,"risk_reward_ratio":payload.get("risk_reward_ratio"),"data_status":payload.get("data_status"),"market_regime":payload.get("market_regime")}}
    finally:db.close()
