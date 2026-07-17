from datetime import datetime, timedelta, timezone
from math import isfinite
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

def _history_eta(db, user_id, limiting: dict | None, relevant_samples: int) -> dict:
    """Honest readiness estimate for the calibration-history gate, measured
    from the actual resolution cadence of the least-supported eligible
    source's exact evidence bucket (user+source+version+symbol+timeframe+
    regime) over the last 6 hours - never a hard-coded 'one per minute'
    assumption. No qualifying resolutions in the window means no ETA."""
    remaining = max(0, settings.active_drive_min_resolved_samples - relevant_samples)
    out = {"samples_remaining": remaining, "current_qualifying_rate_per_hour": None,
           "estimated_minutes_remaining": None, "estimated_ready_at": None,
           "limiting_source": limiting["name"] if limiting else None}
    if limiting is None or not remaining:
        return out
    window_start = datetime.now(timezone.utc) - timedelta(hours=6)
    recent = db.query(PredictionResolution).join(
        PredictionLedger, PredictionResolution.prediction_id == PredictionLedger.prediction_id).filter(
        PredictionLedger.user_id == user_id, PredictionLedger.engine == "active_drive_v2",
        PredictionLedger.source_name == limiting["name"], PredictionLedger.source_version == limiting["version"],
        PredictionLedger.symbol == limiting["symbol"], PredictionLedger.timeframe == limiting["timeframe"],
        PredictionLedger.market_regime == limiting["market_regime"],
        PredictionResolution.resolved_at >= window_start).count()
    if recent:
        rate = recent / 6.0
        minutes = round(remaining / rate * 60)
        out.update(current_qualifying_rate_per_hour=round(rate, 2), estimated_minutes_remaining=minutes,
                   estimated_ready_at=(datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat())
    return out


def _history_counts(db,user_id,symbol,timeframe,regime):
    def count(*filters):
        return db.query(PredictionResolution).join(PredictionLedger,PredictionResolution.prediction_id==PredictionLedger.prediction_id).filter(PredictionLedger.user_id==user_id,PredictionLedger.engine=="active_drive_v2",*filters).count()
    return {"global_resolved":count(),"symbol_resolved":count(PredictionLedger.symbol==symbol),
        "symbol_timeframe_resolved":count(PredictionLedger.symbol==symbol,PredictionLedger.timeframe==timeframe),
        "symbol_timeframe_regime_resolved":count(PredictionLedger.symbol==symbol,PredictionLedger.timeframe==timeframe,PredictionLedger.market_regime==regime)}

# ---------------------------------------------------------------------------
# Current-edge calculation (Phase C/D/E).
#
#   gross_expected_edge = probability_of_win * expected_win_return
#                          - probability_of_loss * expected_loss_return
#   net_expected_edge    = gross_expected_edge - fees - spread - slippage - funding
#
# Units are a decimal return fraction (0.015 == 1.5%), matching every
# ".2%"-formatted expected_edge already consumed by
# app.trading_horizon.service.PROFILES and the frontend. current_edge_supported
# is only True once every condition below passes; otherwise exactly one
# specific edge_block_reason code is recorded - never a generic "blocked".
# Confidence alone never implies a positive edge.
# ---------------------------------------------------------------------------

EDGE_BLOCK_NO_TRADE_DIRECTION = "no_trade_direction"
# An actionable LONG/SHORT that reaches edge evaluation without a usable
# entry, target, or stop. This is a real defect (levels should have been
# generated for an actionable signal), unlike no_trade_direction, which is
# the normal short-circuit for abstentions where levels are intentionally
# absent and must never be reported as missing/invalid.
EDGE_BLOCK_ACTIONABLE_MISSING_LEVELS = "actionable_signal_missing_trade_levels"
EDGE_BLOCK_INVALID_LEVELS = "invalid_entry_target_stop"
EDGE_BLOCK_INVALID_PROBABILITY = "invalid_probability"
EDGE_BLOCK_INSUFFICIENT_SAMPLES = "insufficient_samples"
EDGE_BLOCK_NO_EXPECTANCY = "expectancy_unavailable"
EDGE_BLOCK_STALE_MARKET_DATA = "stale_market_data"
EDGE_BLOCK_STALE_PERFORMANCE_DATA = "stale_performance_data"
EDGE_BLOCK_EDGE_NOT_FINITE = "edge_not_finite"
EDGE_BLOCK_NEGATIVE_GROSS_EDGE = "negative_gross_edge"
EDGE_BLOCK_COSTS_EXCEED_EDGE = "costs_exceed_edge"
EDGE_BLOCK_NET_EDGE_BELOW_THRESHOLD = "net_edge_below_threshold"


def _edge_costs() -> dict:
    return {"fees": settings.active_drive_estimated_taker_fee_rate * 2,  # round-trip: open + close
            "spread": settings.active_drive_estimated_spread_bps / 10_000,
            "slippage": settings.active_drive_estimated_slippage_bps / 10_000,
            "funding": settings.active_drive_estimated_funding_cost_rate}


def _cost_total(costs: dict) -> float:
    return costs["fees"] + costs["spread"] + costs["slippage"] + costs["funding"]


def _entry_target_stop(context: dict) -> tuple[float | None, float | None, float | None]:
    legacy = context.get("legacy") or {}
    def clean(value):
        return float(value) if isinstance(value, (int, float)) and isfinite(value) else None
    return clean(legacy.get("price")), clean(legacy.get("target")), clean(legacy.get("stop"))


def _weighted_mean(pairs: list[tuple[float | None, int]]) -> float | None:
    num = sum(value * n for value, n in pairs if value is not None and n > 0)
    den = sum(n for value, n in pairs if value is not None and n > 0)
    return num / den if den else None


def _aggregate_edge_evidence(directional_sources: list[dict]) -> dict:
    """Combines each currently-eligible candidate's own (already
    most-specific: source+symbol+timeframe+regime) resolved-sample
    performance into one evidence set for the execution-timeframe edge
    calculation - a sample-weighted combination of real per-source
    statistics, not a fabricated figure."""
    total_n = sum(c.get("resolved_samples", 0) or 0 for c in directional_sources)
    win_pairs = [(c.get("average_win_return"), c.get("resolved_samples", 0) or 0) for c in directional_sources]
    loss_pairs = [(c.get("average_loss_return"), c.get("resolved_samples", 0) or 0) for c in directional_sources]
    timestamps = [c["evidence_timestamp"] for c in directional_sources if c.get("evidence_timestamp")]
    return {"resolved": total_n, "average_win_return": _weighted_mean(win_pairs),
            "average_loss_return": _weighted_mean(loss_pairs),
            "evidence_timestamp": max(timestamps) if timestamps else None}


def _current_edge(direction: str, entry: float | None, target: float | None, stop: float | None,
                  probability_up: float | None, edge_evidence: dict) -> dict:
    costs = _edge_costs()
    blocked = lambda code, **extra: {"supported": False, "block_reason": code, "gross_edge": None,
                                      "net_edge": None, "costs": costs, "sample_size": edge_evidence["resolved"], **extra}
    if direction not in ("LONG", "SHORT"):
        return blocked(EDGE_BLOCK_NO_TRADE_DIRECTION)
    if entry is None or entry <= 0 or target is None or stop is None:
        return blocked(EDGE_BLOCK_ACTIONABLE_MISSING_LEVELS,
                       missing=[name for name, v in (("entry", entry), ("target", target), ("stop", stop)) if v is None or (name == "entry" and (v is None or v <= 0))])
    reward_distance = (target - entry) if direction == "LONG" else (entry - target)
    risk_distance = (entry - stop) if direction == "LONG" else (stop - entry)
    if reward_distance <= 0 or risk_distance <= 0:
        return blocked(EDGE_BLOCK_INVALID_LEVELS)
    audit = {"entry": entry, "reward_distance": round(reward_distance, 6), "risk_distance": round(risk_distance, 6)}
    if probability_up is None or not (0.0 < probability_up < 1.0):
        return blocked(EDGE_BLOCK_INVALID_PROBABILITY, **audit)
    if edge_evidence["resolved"] < settings.active_drive_min_resolved_samples:
        return blocked(EDGE_BLOCK_INSUFFICIENT_SAMPLES, **audit)
    if edge_evidence["average_win_return"] is None or edge_evidence["average_loss_return"] is None:
        return blocked(EDGE_BLOCK_NO_EXPECTANCY, **audit)
    evidence_ts = edge_evidence.get("evidence_timestamp")
    if evidence_ts:
        try:
            parsed_ts = datetime.fromisoformat(evidence_ts)
            # SQLite drops tzinfo on round-trip even though resolved_at is
            # always written as UTC-aware (see PredictionResolution model),
            # so a naive result here still means UTC, not local time.
            if parsed_ts.tzinfo is None:
                parsed_ts = parsed_ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - parsed_ts).total_seconds()
        except ValueError:
            age = None
        if age is not None and age > settings.active_drive_max_edge_evidence_age_seconds:
            return blocked(EDGE_BLOCK_STALE_PERFORMANCE_DATA, **audit)
    probability_of_win = probability_up if direction == "LONG" else 1.0 - probability_up
    probability_of_loss = 1.0 - probability_of_win
    expected_win_return = reward_distance / entry
    expected_loss_return = risk_distance / entry
    # Historical realized win/loss magnitudes anchor the return estimate;
    # the current decision's own entry/target/stop only contributes
    # direction-consistent confirmation, not the whole estimate - this
    # avoids inferring an edge purely from one untested price target.
    blended_win_return = (abs(edge_evidence["average_win_return"]) + expected_win_return) / 2
    blended_loss_return = (abs(edge_evidence["average_loss_return"]) + expected_loss_return) / 2
    gross_edge = probability_of_win * blended_win_return - probability_of_loss * blended_loss_return
    net_edge = gross_edge - _cost_total(costs)
    if not isfinite(gross_edge) or not isfinite(net_edge):
        return blocked(EDGE_BLOCK_EDGE_NOT_FINITE, **audit)
    audit_edges = {**audit, "gross_edge": round(gross_edge, 6), "net_edge": round(net_edge, 6)}
    # Three previously-collapsed causes now report distinct codes: the
    # model's own directional estimate is unprofitable before costs
    # (negative_gross_edge); a positive directional estimate is wiped out
    # by round-trip costs (costs_exceed_edge); or it survives costs but
    # doesn't clear the configured minimum-profitability bar
    # (net_edge_below_threshold).
    if gross_edge <= 0:
        return blocked(EDGE_BLOCK_NEGATIVE_GROSS_EDGE, **audit_edges)
    if net_edge <= 0:
        return blocked(EDGE_BLOCK_COSTS_EXCEED_EDGE, **audit_edges)
    if net_edge <= settings.active_drive_min_net_edge:
        return blocked(EDGE_BLOCK_NET_EDGE_BELOW_THRESHOLD, **audit_edges)
    return {"supported": True, "block_reason": None, **audit_edges,
            "costs": costs, "sample_size": edge_evidence["resolved"], "probability_of_win": round(probability_of_win, 4)}


class ActiveDriveV2Engine:
    name=DecisionEngineType.ACTIVE_DRIVE_V2; version="2.2.0"
    def health(self):return {"status":"healthy","failure_policy":"NO_TRADE"}
    def capabilities(self):return ["ml_strategy_quant","bounded_points","bayesian_shrinkage","family_caps","append_only_ledger","no_trade","snapshot_diagnostics"]

    def _candidate(self,db,context,source_type,family,name,version,vote,evidence,shadow=False):
        direction=vote.get("direction","NO_TRADE"); raw=bounded(vote.get("confidence"),0,100)/100; regime=regime_for(context)
        perf=performance(db,context["user_id"],name,version,context["symbol"],context["timeframe"],regime["label"]); sample=min(1.,perf["resolved"]/max(1,settings.active_drive_min_resolved_samples))
        hist=bounded(perf["shrunk_accuracy"]/.5,.6,1.2); recent=bounded(perf["recent_shrunk_accuracy"]/.5,.75,1.15); calibration=.8 if perf["resolved"]<settings.active_drive_min_resolved_samples else 1.
        reliability=bounded((.65+.35*sample)*hist*recent*calibration,.35,1.); sign=1. if direction=="LONG" else -1. if direction=="SHORT" else 0.; base=sign*raw*10; points=base*reliability
        if perf["resolved"]<settings.active_drive_min_resolved_samples:points=max(-2.5,min(2.5,points))
        if shadow:points=0.
        eligible=bool(sign and not shadow); reason=vote.get("reason") or evidence.get("reason") or "Current-data signal"; p_up=.5+sign*raw/2
        return {"source_type":source_type,"family":family,"source_family":family,"name":name,"source_name":name,"version":version,"source_version":version,"symbol":context["symbol"],"timeframe":context["timeframe"],"direction":direction,
          "probability_up":round(p_up,4),"probability_down":round(1-p_up,4),"raw_confidence":round(raw,4),"calibrated_confidence":round(raw*reliability,4),"confidence":round(raw,4),"base_points":round(base,4),
          "sample_size_weight":round(.65+.35*sample,4),"historical_performance_weight":round(hist,4),"symbol_weight":1.,"timeframe_weight":1.,"regime_weight":1.,"recent_performance_weight":round(recent,4),"recency_weight":round(recent,4),"calibration_weight":calibration,"reliability_weight":round(reliability,4),"correlation_penalty":0.,"final_points":round(points,4),"candidate_points":round(points,4),
          "expected_edge":perf["realized_edge"],"realized_edge":perf["realized_edge"],"average_win_return":perf["average_win_return"],"average_loss_return":perf["average_loss_return"],"evidence_timestamp":perf["resolved_at_latest"],"risk_reward_ratio":context.get("risk_reward_ratio"),"market_regime":regime["label"],"evidence_tier":perf["tier"],"resolved_samples":perf["resolved"],"resolved_sample_size":perf["resolved"],"historical_accuracy":perf["accuracy"],"recent_accuracy":perf["recent_accuracy"],"regime_accuracy":None,
          "status":"shadow" if shadow else vote.get("status","eligible" if sign else "rejected"),"eligible":eligible,"eligible_now":eligible,"regime_compatible":True,"required_data_available":not("unavailable" in reason.lower()),"rejection_code":None if eligible else _rejection_code(reason,shadow),"rejection_reason":None if eligible else reason,"reason":reason,"evidence":evidence,"data_freshness":context.get("data_status","live")}

    def evaluate(self,context):
        db=context["db"]; context={**context,"user_id":context.get("user_id") or settings.admin_username}; legacy=context["legacy"]; candidates=[]
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
        regime=regime_for(context); histories=_history_counts(db,context["user_id"],context["symbol"],context["timeframe"],regime["label"]); directional_sources=[c for c in candidates if c["eligible"]]
        limiting_source=min(directional_sources,key=lambda c:c["resolved_samples"],default=None)
        relevant_samples=limiting_source["resolved_samples"] if limiting_source else 0; history_pass=relevant_samples>=settings.active_drive_min_resolved_samples
        history_eta=_history_eta(db,context["user_id"],
            {"name":limiting_source["name"],"version":limiting_source["version"],"symbol":context["symbol"],
             "timeframe":context["timeframe"],"market_regime":regime["label"]} if limiting_source else None,
            relevant_samples)
        raw_up=round(.5+signed/max(evidence,1)*.5,4)
        preliminary_direction="LONG" if signed>0 else "SHORT" if signed<0 else "NO_TRADE"
        entry,target,stop=_entry_target_stop(context); edge_evidence=_aggregate_edge_evidence(directional_sources)
        # Validation order matters: gates that are independent of trade
        # levels (data freshness, evidence, margin, calibration history,
        # confidence, risk/reward) run first. Only a decision that is still
        # actionable after those gates has its entry/target/stop and edge
        # evaluated - an abstention's absent levels are intentional, not a
        # defect, and must short-circuit as no_trade_direction rather than
        # surface as invalid/missing-level errors.
        blockers=[]
        if context.get("data_status")=="stale":blockers.append("Market data is stale")
        if evidence<settings.active_drive_min_total_evidence:blockers.append("Insufficient total evidence")
        if margin<settings.active_drive_min_point_margin:blockers.append("Point margin below required threshold")
        if not history_pass: blockers.append(f"Calibrated directional confidence not established: relevant source history {relevant_samples}/{settings.active_drive_min_resolved_samples}")
        elif directional is None or directional<settings.active_drive_min_confidence:blockers.append("Calibrated directional confidence below threshold")
        if context.get("risk_reward_ratio") is None:blockers.append("Risk/reward is unavailable")
        if context.get("data_status")=="stale":
            edge_result={"supported":False,"block_reason":EDGE_BLOCK_STALE_MARKET_DATA,"gross_edge":None,"net_edge":None,"costs":_edge_costs(),"sample_size":edge_evidence["resolved"]}
        elif blockers or preliminary_direction not in ("LONG","SHORT"):
            edge_result={"supported":False,"block_reason":EDGE_BLOCK_NO_TRADE_DIRECTION,"gross_edge":None,"net_edge":None,"costs":_edge_costs(),"sample_size":edge_evidence["resolved"]}
        else:
            edge_result=_current_edge(preliminary_direction,entry,target,stop,raw_up if history_pass else None,edge_evidence)
        if not edge_result["supported"] and edge_result["block_reason"]!=EDGE_BLOCK_NO_TRADE_DIRECTION:blockers.append(f"Current edge is not supported: {edge_result['block_reason']}")
        signal="NO_TRADE" if blockers else ("LONG" if signed>0 else "SHORT"); decision_conf=directional if signal in ("LONG","SHORT") else None; now=datetime.now(timezone.utc); eligible=signal!="NO_TRADE"
        confidence_failure=None if history_pass else {"code":"INSUFFICIENT_CALIBRATION_HISTORY","reason":f"Only {relevant_samples} resolved predictions are available for the least-supported eligible source in {context['symbol']} {context['timeframe']} {regime['label']}; {settings.active_drive_min_resolved_samples} are required."}
        metrics={
          "evidence":{"name":"total_evidence","value":round(evidence,3),"required":settings.active_drive_min_total_evidence,"passed":evidence>=settings.active_drive_min_total_evidence,"scope":f"{context['symbol']} {context['timeframe']}","formula":"sum(abs(family_points_after_cap))","description":"Total usable signal strength after reliability weighting and family caps.","contributions":[{"family":k,"absolute_points":round(abs(v),4)} for k,v in totals.items()]},
          "point_margin":{"name":"point_margin","value":round(margin,3),"required":settings.active_drive_min_point_margin,"passed":margin>=settings.active_drive_min_point_margin,"scope":f"{context['symbol']} {context['timeframe']}","formula":"abs(long_points - short_points)","description":"Directional lead after family caps.","long_points":round(long_points,3),"short_points":round(short_points,3),"winning_direction":"LONG" if signed>0 else "SHORT" if signed<0 else "NONE"},
          "history":{"name":"relevant_history","value":relevant_samples,"required":settings.active_drive_min_resolved_samples,"passed":history_pass,"scope":f"eligible source/version · {context['symbol']} {context['timeframe']} · {regime['label']}","formula":"minimum resolved count across currently eligible authoritative sources","description":"Conservative source-specific calibration coverage.",**histories,**history_eta},
          "confidence":{"name":"directional_confidence","value":round(directional,4) if history_pass and directional is not None else None,"raw_value":round(directional,4) if directional is not None else None,"required":settings.active_drive_min_confidence,"passed":bool(history_pass and directional is not None and directional>=settings.active_drive_min_confidence),"scope":f"{context['symbol']} {context['timeframe']} · decision snapshot","formula":"abs(long_points-short_points)/total_evidence, available only after relevant calibration history","description":"Calibrated directional separation, not a champion-model probability.","failure":confidence_failure},
          "edge":{"name":"current_net_edge","value":edge_result["net_edge"],"required":settings.active_drive_min_net_edge,"passed":edge_result["supported"],"scope":f"{context['symbol']} {context['timeframe']} · execution authority","formula":"probability_of_win*blended_win_return - probability_of_loss*blended_loss_return - (fees+spread+slippage+funding)","description":"Net expected edge after estimated round-trip costs, from resolved out-of-sample history blended with the current entry/target/stop.","gross_edge":edge_result["gross_edge"],"sample_size":edge_evidence["resolved"],"required_sample_size":settings.active_drive_min_resolved_samples,"block_reason":edge_result["block_reason"],"costs":edge_result["costs"]},
        }
        ranked=sorted(candidates,key=lambda c:abs(c["final_points"]),reverse=True); resolved=max((c["resolved_samples"] for c in candidates),default=0)
        return {"decision_id":None,"engine":self.name.value,"engine_info":{"id":self.name.value,"name":"Active Drive V2","version":self.version,"authoritative":True},"engine_version":self.version,"decision_method":"weighted_ensemble","symbol":context["symbol"],"timeframe":context["timeframe"],"generated_at":now.isoformat(),"expires_at":(now+timedelta(seconds=60)).isoformat(),"performance_snapshot_revision":now.isoformat(),"market_data_revision":legacy.get("data_quality",{}).get("last_candle_time"),
          "signal":signal,"final_signal":signal,"directional_confidence":round(decision_conf,4) if decision_conf is not None else None,"decision_confidence":round(decision_conf,4) if decision_conf is not None else None,"abstention_confidence":round(min(1.,len(blockers)/4),4) if signal=="NO_TRADE" else None,"confidence":round(decision_conf,4) if decision_conf is not None else None,"confidence_diagnostics":{"raw_probability_up":champion.get("p_up"),"raw_probability_down":1-champion.get("p_up") if isinstance(champion.get("p_up"),(int,float)) else None,"combined_probability_up":raw_up if history_pass else None,"combined_probability_down":round(1-raw_up,4) if history_pass else None,"indicative_point_score_up":raw_up,"indicative_point_score_down":round(1-raw_up,4),"calibrated_directional_confidence":metrics["confidence"]["value"],"required_confidence":settings.active_drive_min_confidence,"passed":metrics["confidence"]["passed"],"eligible_probability_sources":len(directional_sources),"required_probability_sources":1,"calibration_sample_count":relevant_samples,"minimum_calibration_samples":settings.active_drive_min_resolved_samples,"failure_code":confidence_failure["code"] if confidence_failure else None,"failure_reason":confidence_failure["reason"] if confidence_failure else None},
          "required_confidence":settings.active_drive_min_confidence,"minimum_total_evidence":settings.active_drive_min_total_evidence,"required_point_margin":settings.active_drive_min_point_margin,"probability_up":raw_up if history_pass else None,"probability_down":round(1-raw_up,4) if history_pass else None,
          "expected_edge":edge_result["net_edge"],"gross_expected_edge":edge_result["gross_edge"],"net_expected_edge":edge_result["net_edge"],"current_edge_supported":edge_result["supported"],"edge_supported":edge_result["supported"],"edge_block_reason":edge_result["block_reason"],"edge_costs":edge_result["costs"],"edge_sample_size":edge_evidence["resolved"],"edge_source":"aggregated_eligible_sources" if edge_evidence["resolved"] else "none","edge_evidence_timestamp":edge_evidence["evidence_timestamp"],"expected_value":edge_result["net_edge"],"risk_reward_ratio":context.get("risk_reward_ratio"),
          "entry_price":edge_result.get("entry"),"reward_distance":edge_result.get("reward_distance"),"risk_distance":edge_result.get("risk_distance"),
          "recommended_target":legacy.get("target") if signal in ("LONG","SHORT") else None,"recommended_stop":legacy.get("stop") if signal in ("LONG","SHORT") else None,
          "long_points":round(long_points,3),"short_points":round(short_points,3),"neutral_points":0.,"point_margin":round(margin,3),"total_evidence":round(evidence,3),"evidence_tier":"insufficient_evidence" if not history_pass else "early_evidence","eligible_for_execution":eligible,"blocking_reasons":blockers,"execution":{"eligible":eligible,"risk_gate_passed":False,"reason":blockers[0] if blockers else None},"decision_metrics":metrics,
          "top_supporting_sources":[c for c in ranked if c["final_points"]>0][:5],"top_conflicting_sources":[c for c in ranked if c["final_points"]<0][:5],"supporting_sources":[c for c in ranked if c["final_points"]>0][:5],"conflicting_sources":[c for c in ranked if c["final_points"]<0][:5],"candidates":candidates,"family_totals":totals,"market_regime":regime,"data_status":{"fresh":context.get("data_status")=="live","stale":context.get("data_status")=="stale","age_seconds":context.get("data_age_seconds",0),"source":context.get("data_status","live")},"history":{"resolved_sample_count":resolved,"relevant_resolved_sample_count":relevant_samples,"evidence_status":"insufficient" if not history_pass else "early","performance_updated_at":now.isoformat(),**histories},"candidate_count":len(candidates)}
