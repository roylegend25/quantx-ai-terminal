from datetime import datetime, timedelta, timezone
from app.core.config import settings
from app.db.models import PredictionLedger, PredictionResolution
from app.decision_engine.repository import performance
from app.decision_engine.sources import quant_votes, strategy_votes
from app.decision_engine.types import DecisionEngineType

LEGACY_FAMILIES={"trend":"trend","momentum":"momentum","breakout":"breakout","mean_reversion":"mean_reversion"}
SHADOW_MODELS=(("logistic_regression","linear_ml"),("elastic_net_logistic","linear_ml"),("extra_trees","tree_ml"),("hist_gradient_boosting","tree_ml"),("calibrated_linear_svm","linear_ml"),("sgd_classifier","linear_ml"),("soft_voting_ensemble","ensemble_ml"),("stacking_meta_classifier","ensemble_ml"))

def bounded(value,low=0.,high=1.):
    try:return max(low,min(high,float(value)))
    except (TypeError,ValueError):return low

def regime_for(context):
    raw=context.get("regime"); text=str(raw or "unknown").lower(); f=context.get("legacy",{}).get("features") or {}
    trend="bullish" if "bull" in text or text=="trending" else "bearish" if "bear" in text else "range" if "rang" in text else "neutral"
    rv=f.get("realized_volatility"); bbw=f.get("bb_width"); volatility="high" if isinstance(rv,(int,float)) and rv>.03 else "compressed" if isinstance(bbw,(int,float)) and bbw<.008 else "normal"
    label=f"{volatility.title()}-volatility {trend} {'trend' if trend in ('bullish','bearish') else 'market'}"
    return {"trend":trend,"volatility":volatility,"liquidity":"normal","derivatives":"neutral","label":label,"legacy":raw}

def _rejection_code(reason,shadow=False):
    text=(reason or "").lower()
    if shadow:return "SHADOW_ONLY"
    if "stale" in text:return "DATA_STALE"
    if "unavailable" in text or "missing" in text:return "REQUIRED_INDICATOR_MISSING"
    if "volatility too high" in text:return "REGIME_MISMATCH"
    if "confirm" in text:return "CONFIRMATION_MISSING"
    return "NO_TRIGGER"

def _history_counts(db,symbol,timeframe,regime):
    def count(*filters):
        return db.query(PredictionResolution).join(PredictionLedger,PredictionResolution.prediction_id==PredictionLedger.prediction_id).filter(*filters).count()
    return {"global_resolved":count(),"symbol_resolved":count(PredictionLedger.symbol==symbol),
        "symbol_timeframe_resolved":count(PredictionLedger.symbol==symbol,PredictionLedger.timeframe==timeframe),
        "symbol_timeframe_regime_resolved":count(PredictionLedger.symbol==symbol,PredictionLedger.timeframe==timeframe,PredictionLedger.market_regime==regime)}

class ActiveDriveV2Engine:
    name=DecisionEngineType.ACTIVE_DRIVE_V2; version="2.2.0"
    def health(self):return {"status":"healthy","failure_policy":"NO_TRADE"}
    def capabilities(self):return ["ml_strategy_quant","bounded_points","bayesian_shrinkage","family_caps","append_only_ledger","no_trade","snapshot_diagnostics"]

    def _candidate(self,db,context,source_type,family,name,version,vote,evidence,shadow=False):
        direction=vote.get("direction","NO_TRADE"); raw=bounded(vote.get("confidence"),0,100)/100; regime=regime_for(context)
        perf=performance(db,name,version,context["symbol"],context["timeframe"],regime["label"]); sample=min(1.,perf["resolved"]/max(1,settings.active_drive_min_resolved_samples))
        hist=bounded(perf["shrunk_accuracy"]/.5,.6,1.2); recent=bounded(perf["recent_shrunk_accuracy"]/.5,.75,1.15); calibration=.8 if perf["resolved"]<settings.active_drive_min_resolved_samples else 1.
        reliability=bounded((.65+.35*sample)*hist*recent*calibration,.35,1.); sign=1. if direction=="LONG" else -1. if direction=="SHORT" else 0.; base=sign*raw*10; points=base*reliability
        if perf["resolved"]<settings.active_drive_min_resolved_samples:points=max(-2.5,min(2.5,points))
        if shadow:points=0.
        eligible=bool(sign and not shadow); reason=vote.get("reason") or evidence.get("reason") or "Current-data signal"; p_up=.5+sign*raw/2
        return {"source_type":source_type,"family":family,"source_family":family,"name":name,"source_name":name,"version":version,"source_version":version,"symbol":context["symbol"],"timeframe":context["timeframe"],"direction":direction,
          "probability_up":round(p_up,4),"probability_down":round(1-p_up,4),"raw_confidence":round(raw,4),"calibrated_confidence":round(raw*reliability,4),"confidence":round(raw,4),"base_points":round(base,4),
          "sample_size_weight":round(.65+.35*sample,4),"historical_performance_weight":round(hist,4),"symbol_weight":1.,"timeframe_weight":1.,"regime_weight":1.,"recent_performance_weight":round(recent,4),"recency_weight":round(recent,4),"calibration_weight":calibration,"reliability_weight":round(reliability,4),"correlation_penalty":0.,"final_points":round(points,4),"candidate_points":round(points,4),
          "expected_edge":None,"realized_edge":perf["realized_edge"],"risk_reward_ratio":context.get("risk_reward_ratio"),"market_regime":regime["label"],"evidence_tier":perf["tier"],"resolved_samples":perf["resolved"],"resolved_sample_size":perf["resolved"],"historical_accuracy":perf["accuracy"],"recent_accuracy":perf["recent_accuracy"],"regime_accuracy":None,
          "status":"shadow" if shadow else vote.get("status","eligible" if sign else "rejected"),"eligible":eligible,"eligible_now":eligible,"regime_compatible":True,"required_data_available":not("unavailable" in reason.lower()),"rejection_code":None if eligible else _rejection_code(reason,shadow),"rejection_reason":None if eligible else reason,"reason":reason,"evidence":evidence,"data_freshness":context.get("data_status","live")}

    def evaluate(self,context):
        db=context["db"]; legacy=context["legacy"]; candidates=[]
        for name,vote in (legacy.get("strategies") or {}).items():candidates.append(self._candidate(db,context,"strategy",LEGACY_FAMILIES.get(name,"strategy"),name,"1.0.0",vote,{"reason":vote.get("reason")}))
        for name,family,vote in strategy_votes(legacy.get("features") or {}):candidates.append(self._candidate(db,context,"strategy",family,name,"2.1.0",vote,{"reason":vote["reason"]}))
        champion=legacy.get("ml_champion") or {}
        if champion.get("used"):candidates.append(self._candidate(db,context,"ml","tree_ml",champion.get("model_name") or "champion_ml",champion.get("version") or "unknown",champion,{"model_id":champion.get("model_id")}))
        for name,family in SHADOW_MODELS:candidates.append(self._candidate(db,context,"ml",family,name,"shadow-1",{"direction":"NO_TRADE","confidence":0,"reason":"Shadow source; no validated inference this cycle"},{"capability":"shadow"},True))
        for name,family,vote,evidence in quant_votes(legacy.get("features") or {}):candidates.append(self._candidate(db,context,"quant",family,name,"2.1.0",vote,evidence))
        raw_family={}
        for c in candidates:raw_family[c["family"]]=raw_family.get(c["family"],0.)+c["final_points"]
        totals={k:max(-settings.active_drive_family_cap,min(settings.active_drive_family_cap,v)) for k,v in raw_family.items()}
        long_points=sum(max(0,v) for v in totals.values()); short_points=sum(max(0,-v) for v in totals.values()); evidence=long_points+short_points; margin=abs(long_points-short_points); directional=margin/evidence if evidence else None; signed=long_points-short_points
        regime=regime_for(context); histories=_history_counts(db,context["symbol"],context["timeframe"],regime["label"]); directional_sources=[c for c in candidates if c["eligible"]]
        relevant_samples=min((c["resolved_samples"] for c in directional_sources),default=0); history_pass=relevant_samples>=settings.active_drive_min_resolved_samples
        blockers=[]
        if context.get("data_status")=="stale":blockers.append("Market data is stale")
        if evidence<settings.active_drive_min_total_evidence:blockers.append("Insufficient total evidence")
        if margin<settings.active_drive_min_point_margin:blockers.append("Point margin below required threshold")
        if not history_pass: blockers.append(f"Calibrated directional confidence not established: relevant source history {relevant_samples}/{settings.active_drive_min_resolved_samples}")
        elif directional is None or directional<settings.active_drive_min_confidence:blockers.append("Calibrated directional confidence below threshold")
        blockers.append("Expected edge is not yet supported by resolved out-of-sample history")
        if context.get("risk_reward_ratio") is None:blockers.append("Risk/reward is unavailable")
        signal="NO_TRADE" if blockers else ("LONG" if signed>0 else "SHORT"); decision_conf=directional if signal in ("LONG","SHORT") else None; now=datetime.now(timezone.utc); eligible=signal!="NO_TRADE"
        raw_up=round(.5+signed/max(evidence,1)*.5,4); confidence_failure=None if history_pass else {"code":"INSUFFICIENT_CALIBRATION_HISTORY","reason":f"Only {relevant_samples} resolved predictions are available for the least-supported eligible source in {context['symbol']} {context['timeframe']} {regime['label']}; {settings.active_drive_min_resolved_samples} are required."}
        metrics={
          "evidence":{"name":"total_evidence","value":round(evidence,3),"required":settings.active_drive_min_total_evidence,"passed":evidence>=settings.active_drive_min_total_evidence,"scope":f"{context['symbol']} {context['timeframe']}","formula":"sum(abs(family_points_after_cap))","description":"Total usable signal strength after reliability weighting and family caps.","contributions":[{"family":k,"absolute_points":round(abs(v),4)} for k,v in totals.items()]},
          "point_margin":{"name":"point_margin","value":round(margin,3),"required":settings.active_drive_min_point_margin,"passed":margin>=settings.active_drive_min_point_margin,"scope":f"{context['symbol']} {context['timeframe']}","formula":"abs(long_points - short_points)","description":"Directional lead after family caps.","long_points":round(long_points,3),"short_points":round(short_points,3),"winning_direction":"LONG" if signed>0 else "SHORT" if signed<0 else "NONE"},
          "history":{"name":"relevant_history","value":relevant_samples,"required":settings.active_drive_min_resolved_samples,"passed":history_pass,"scope":f"eligible source/version · {context['symbol']} {context['timeframe']} · {regime['label']}","formula":"minimum resolved count across currently eligible authoritative sources","description":"Conservative source-specific calibration coverage.",**histories},
          "confidence":{"name":"directional_confidence","value":round(directional,4) if history_pass and directional is not None else None,"raw_value":round(directional,4) if directional is not None else None,"required":settings.active_drive_min_confidence,"passed":bool(history_pass and directional is not None and directional>=settings.active_drive_min_confidence),"scope":f"{context['symbol']} {context['timeframe']} · decision snapshot","formula":"abs(long_points-short_points)/total_evidence, available only after relevant calibration history","description":"Calibrated directional separation, not a champion-model probability.","failure":confidence_failure},
        }
        ranked=sorted(candidates,key=lambda c:abs(c["final_points"]),reverse=True); resolved=max((c["resolved_samples"] for c in candidates),default=0)
        return {"decision_id":None,"engine":self.name.value,"engine_info":{"id":self.name.value,"name":"Active Drive V2","version":self.version,"authoritative":True},"engine_version":self.version,"decision_method":"weighted_ensemble","symbol":context["symbol"],"timeframe":context["timeframe"],"generated_at":now.isoformat(),"expires_at":(now+timedelta(seconds=60)).isoformat(),"performance_snapshot_revision":now.isoformat(),"market_data_revision":legacy.get("data_quality",{}).get("last_candle_time"),
          "signal":signal,"final_signal":signal,"directional_confidence":round(decision_conf,4) if decision_conf is not None else None,"decision_confidence":round(decision_conf,4) if decision_conf is not None else None,"abstention_confidence":round(min(1.,len(blockers)/4),4) if signal=="NO_TRADE" else None,"confidence":round(decision_conf,4) if decision_conf is not None else None,"confidence_diagnostics":{"raw_probability_up":champion.get("p_up"),"raw_probability_down":1-champion.get("p_up") if isinstance(champion.get("p_up"),(int,float)) else None,"combined_probability_up":raw_up if history_pass else None,"combined_probability_down":round(1-raw_up,4) if history_pass else None,"indicative_point_score_up":raw_up,"indicative_point_score_down":round(1-raw_up,4),"calibrated_directional_confidence":metrics["confidence"]["value"],"required_confidence":settings.active_drive_min_confidence,"passed":metrics["confidence"]["passed"],"eligible_probability_sources":len(directional_sources),"required_probability_sources":1,"calibration_sample_count":relevant_samples,"minimum_calibration_samples":settings.active_drive_min_resolved_samples,"failure_code":confidence_failure["code"] if confidence_failure else None,"failure_reason":confidence_failure["reason"] if confidence_failure else None},
          "required_confidence":settings.active_drive_min_confidence,"minimum_total_evidence":settings.active_drive_min_total_evidence,"required_point_margin":settings.active_drive_min_point_margin,"probability_up":raw_up if history_pass else None,"probability_down":round(1-raw_up,4) if history_pass else None,"expected_edge":None,"expected_value":None,"risk_reward_ratio":context.get("risk_reward_ratio"),"recommended_target":legacy.get("target") if signal in ("LONG","SHORT") else None,"recommended_stop":legacy.get("stop") if signal in ("LONG","SHORT") else None,
          "long_points":round(long_points,3),"short_points":round(short_points,3),"neutral_points":0.,"point_margin":round(margin,3),"total_evidence":round(evidence,3),"evidence_tier":"insufficient_evidence" if not history_pass else "early_evidence","eligible_for_execution":eligible,"blocking_reasons":blockers,"execution":{"eligible":eligible,"risk_gate_passed":False,"reason":blockers[0] if blockers else None},"decision_metrics":metrics,
          "top_supporting_sources":[c for c in ranked if c["final_points"]>0][:5],"top_conflicting_sources":[c for c in ranked if c["final_points"]<0][:5],"supporting_sources":[c for c in ranked if c["final_points"]>0][:5],"conflicting_sources":[c for c in ranked if c["final_points"]<0][:5],"candidates":candidates,"family_totals":totals,"market_regime":regime,"data_status":{"fresh":context.get("data_status")=="live","stale":context.get("data_status")=="stale","age_seconds":context.get("data_age_seconds",0),"source":context.get("data_status","live")},"history":{"resolved_sample_count":resolved,"relevant_resolved_sample_count":relevant_samples,"evidence_status":"insufficient" if not history_pass else "early","performance_updated_at":now.isoformat(),**histories},"candidate_count":len(candidates)}
