"""Authenticated, read-only Active Drive diagnostics from one persisted snapshot."""
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from types import SimpleNamespace
import time
from app.core.config import settings
from app.core.deps import get_current_user
from app.db.models import ActiveDriveDecision, MarketCandle, PredictionCycle, PredictionLedger, PredictionResolution, SignalCandidateRecord
from app.db.session import SessionLocal
from app.decision_engine import scheduler as resolver_scheduler
from app.decision_engine.repository import owner
from app.decision_engine.ledger import HORIZON_SECONDS
from app.decision_engine.v2 import SHADOW_MODELS
from app.quant.forecast import TIMEFRAME_SECONDS
from app.timeframes.canonical import parse_timeframe

router=APIRouter(prefix="/api/analysis",tags=["analysis"])
QUANT_INPUTS={
 "regression_slope_proxy":["ema20","ema50"],"kalman_trend_proxy":["ema20","ema50"],"mean_reversion_zscore":["price","ema20","atr"],"atr_expected_move":["price","atr"],
 "realized_volatility":["realized_volatility"],"compression_state":["bb_width"],"volume_anomaly":["volume","volume_sma20"],"persistence_proxy":["rsi"],
 "funding_divergence":["funding_rate","price","ema20"],"open_interest_divergence":["oi_change_pct","cvd"],"order_book_imbalance":["bid_ask_ratio"],"correlation_beta_context":["btc_eth_history"]}
MISSING_CODES={"funding_divergence":"MISSING_FUNDING_RATE","open_interest_divergence":"MISSING_OPEN_INTEREST_CHANGE","order_book_imbalance":"MISSING_ORDER_BOOK_DEPTH","correlation_beta_context":"MISSING_CROSS_ASSET_HISTORY"}
# Canonical prediction timeframes, 1m through one calendar month. 1M is a
# calendar month and must never be conflated with 1m/30m. Non-canonical or
# NULL timeframes are reported in a separate legacy section, never as a row
# of the primary per-timeframe axis.
TIMEFRAMES=["1m","3m","5m","15m","30m","1h","4h","1d","1w","1M"]
LEGACY_KEY="legacy_unattributed"

def _iso(v):return v.isoformat() if v else None

def _naive_utc_now():
    """DB datetimes round-trip through SQLite tz-naive; compare like with like."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _unresolved_reason(ledger,now,candle_latest_ms,resolver_running):
    """Structured reason one prediction has not resolved. Prefers the
    Phase 33 resolver's own persisted classification (app.decision_engine.
    resolver) when available - it reflects the actual last backfill
    attempt/backoff state, which this on-the-fly heuristic can't see -
    and falls back to computing one fresh for legacy rows the resolver
    hasn't touched yet (predate the unresolved_reason column, or a row
    that's due but no resolver cycle has processed it yet)."""
    if ledger.reference_price is None: return "missing_entry_price"
    if ledger.timeframe not in TIMEFRAMES: return "unsupported_timeframe"
    deadline=ledger.resolution_deadline
    if deadline is None: return "invalid_due_time"
    if deadline.tzinfo is not None: deadline=deadline.replace(tzinfo=None)
    if deadline>now: return "awaiting_horizon"
    if getattr(ledger,"unresolved_reason",None): return ledger.unresolved_reason
    latest=candle_latest_ms.get((ledger.symbol,ledger.timeframe))
    if latest is None or latest<int(deadline.replace(tzinfo=timezone.utc).timestamp()*1000):
        tf_ms=TIMEFRAME_SECONDS.get(ledger.timeframe,300)*1000
        stale=latest is None or (int(now.replace(tzinfo=timezone.utc).timestamp()*1000)-latest)>2*tf_ms+300_000
        return "market_data_gap" if stale else "awaiting_future_candle"
    return "resolver_delayed" if resolver_running else "resolver_not_running"

def _group(rows,key_fn,now=None,candle_latest_ms=None,resolver_running=True,with_reasons=False):
    groups=defaultdict(list)
    for ledger,resolution in rows:groups[str(key_fn(ledger) or LEGACY_KEY)].append((ledger,resolution))
    out=[]
    for key,items in sorted(groups.items()):
        resolved=[(l,r) for l,r in items if r is not None]; correct=sum(r.correct is True for _,r in resolved); wrong=sum(r.correct is False for _,r in resolved); neutral=sum(bool(r.neutral_result) for _,r in resolved); directional=correct+wrong; returns=[r.actual_return for _,r in resolved if r.actual_return is not None]
        row={"key":key,"total_predictions":len(items),"resolved":len(resolved),"unresolved":len(items)-len(resolved),"correct":correct,"wrong":wrong,"neutral":neutral,"accuracy":round(correct/directional,4) if directional>=20 else None,"neutral_rate":round(neutral/len(resolved),4) if resolved else None,"average_realized_return":round(sum(returns)/len(returns),8) if returns else None,"expected_edge_sample_count":directional,"first_prediction":_iso(min((l.generated_at for l,_ in items),default=None)),"latest_prediction":_iso(max((l.generated_at for l,_ in items),default=None))}
        if with_reasons and now is not None:
            unresolved=[l for l,r in items if r is None]
            reasons=Counter(_unresolved_reason(l,now,candle_latest_ms or {},resolver_running) for l in unresolved)
            resolved_ats=[r.resolved_at for _,r in resolved if r.resolved_at is not None]
            delays=[(r.resolved_at-l.resolution_deadline).total_seconds() for l,r in resolved if r.resolved_at is not None and l.resolution_deadline is not None]
            pending=[l.resolution_deadline for l in unresolved if l.resolution_deadline is not None and l.reference_price is not None]
            row.update({"unresolved_reasons":dict(reasons),
                "first_resolved_at":_iso(min(resolved_ats,default=None)),"latest_resolved_at":_iso(max(resolved_ats,default=None)),
                "oldest_unresolved_at":_iso(min((l.generated_at for l in unresolved),default=None)),
                "next_resolution_at":_iso(min((d for d in pending if d>now),default=None)),
                "average_resolution_delay_seconds":round(sum(delays)/len(delays)) if delays else None,
                "expected_horizon_seconds":HORIZON_SECONDS.get(key) if key in HORIZON_SECONDS else None,
                "relevant_calibration_samples":directional,"required_calibration_samples":20,
                "readiness_status":"ready" if directional>=20 else "warming_up" if directional else "no_resolved_samples"})
        out.append(row)
    return out

def _filtered_rows(db,symbol,timeframe,engine,source_type,source_name,source_version,market_regime,date_from,date_to,cycle_id=None):
    q=db.query(PredictionLedger,PredictionResolution).outerjoin(PredictionResolution,PredictionResolution.prediction_id==PredictionLedger.prediction_id)
    for field,value in ((PredictionLedger.symbol,symbol.upper() if symbol else None),(PredictionLedger.timeframe,parse_timeframe(timeframe).value if timeframe else None),(PredictionLedger.engine,engine),(PredictionLedger.source_type,source_type),(PredictionLedger.source_name,source_name),(PredictionLedger.source_version,source_version),(PredictionLedger.market_regime,market_regime),(PredictionLedger.cycle_id,cycle_id)):
        if value:q=q.filter(field==value)
    if date_from:q=q.filter(PredictionLedger.generated_at>=date_from)
    if date_to:q=q.filter(PredictionLedger.generated_at<=date_to)
    return q.all()

@router.get("/prediction-resolution-summary")
def prediction_resolution_summary(symbol:str|None=None,timeframe:str|None=None,engine:str|None=None,source_type:str|None=None,source_name:str|None=None,source_version:str|None=None,market_regime:str|None=None,date_from:datetime|None=None,date_to:datetime|None=None,cycle_id:str|None=None):
    db=SessionLocal()
    try:
        try: rows=_filtered_rows(db,symbol,timeframe,engine,source_type,source_name,source_version,market_regime,date_from,date_to,cycle_id)
        except ValueError as exc: raise HTTPException(422,{"code":"UNSUPPORTED_TIMEFRAME","message":"Unsupported timeframe."}) from exc
        resolved=[(l,r) for l,r in rows if r is not None]; correct=sum(r.correct is True for _,r in resolved); wrong=sum(r.correct is False for _,r in resolved); neutral=sum(bool(r.neutral_result) for _,r in resolved); now=_naive_utc_now()
        expired=sum(r is None and l.resolution_deadline < now-timedelta(seconds=TIMEFRAME_SECONDS.get(l.timeframe,300)) for l,r in rows)
        candle_latest_ms={(s,tf):ms for s,tf,ms in db.query(MarketCandle.symbol,MarketCandle.timeframe,func.max(MarketCandle.timestamp)).group_by(MarketCandle.symbol,MarketCandle.timeframe)}
        resolver_running=bool(resolver_scheduler.status().get("running"))
        canonical=[(l,r) for l,r in rows if l.timeframe in TIMEFRAMES]; legacy=[(l,r) for l,r in rows if l.timeframe not in TIMEFRAMES]
        by_tf=_group(canonical,lambda l:l.timeframe,now=now,candle_latest_ms=candle_latest_ms,resolver_running=resolver_running,with_reasons=True)
        present={x["key"] for x in by_tf}; by_tf.extend({"key":tf,"total_predictions":0,"resolved":0,"unresolved":0,"correct":0,"wrong":0,"neutral":0,"accuracy":None,"neutral_rate":None,"average_realized_return":None,"expected_edge_sample_count":0,"first_prediction":None,"latest_prediction":None,"unresolved_reasons":{},"first_resolved_at":None,"latest_resolved_at":None,"oldest_unresolved_at":None,"next_resolution_at":None,"average_resolution_delay_seconds":None,"expected_horizon_seconds":HORIZON_SECONDS.get(tf),"relevant_calibration_samples":0,"required_calibration_samples":20,"readiness_status":"no_predictions"} for tf in TIMEFRAMES if tf not in present)
        unresolved_reasons=Counter(_unresolved_reason(l,now,candle_latest_ms,resolver_running) for l,r in rows if r is None)
        oldest_unresolved=min((l.generated_at for l,r in rows if r is None),default=None)
        return {"total_predictions":len(rows),"resolved":len(resolved),"unresolved":len(rows)-len(resolved),"expired_unresolved":expired,"correct":correct,"wrong":wrong,"neutral":neutral,
          "neutral_threshold":settings.resolution_neutral_band,"neutral_threshold_units":"decimal_return_fraction",
          "unresolved_reasons":dict(unresolved_reasons),"oldest_unresolved_at":_iso(oldest_unresolved),
          "timeframes":TIMEFRAMES,
          "legacy":{"total_predictions":len(legacy),"note":"Rows whose timeframe is not a canonical prediction timeframe; excluded from the primary axis and from current calibration.","rows":_group(legacy,lambda l:l.timeframe)},
          "by_symbol":_group(rows,lambda l:l.symbol),"by_timeframe":sorted(by_tf,key=lambda x:TIMEFRAMES.index(x["key"]) if x["key"] in TIMEFRAMES else 99),"by_symbol_timeframe":_group(canonical,lambda l:f"{l.symbol} {l.timeframe}"),"by_engine":_group(rows,lambda l:l.engine),"by_source_type":_group(rows,lambda l:l.source_type),"by_source":_group(rows,lambda l:f"{l.source_type}:{l.source_name}:{l.source_version}"),"by_regime":_group(rows,lambda l:l.market_regime)}
    finally:db.close()

# Phase 34: performance rework of resolver-health (was doing an unbounded
# .all() over every overdue row - 15-20s+, observed timing out past 30s
# under real concurrent write load, once the backlog reached ~16-18k rows
# in production-scale testing). Two things changed:
#  1. due_count/not_due_count/overdue_count/oldest_overdue_at stay EXACT
#     always, computed via one cheap indexed COUNT+MIN per timeframe
#     partition (_overdue_partitions) - no GROUP BY, no row hydration.
#     A single query with a per-row CASE expression for the grace-period
#     comparison was tried first and measured 3x slower at 100k+ rows
#     (EXPLAIN QUERY PLAN showed it couldn't use the timeframe or
#     resolution_deadline index at all); per-partition constant thresholds
#     let each one index-seek directly.
#  2. unresolved_reason_counts is now an explicitly bounded, honestly
#     labeled SAMPLE (at most _UNCLASSIFIED_SAMPLE_LIMIT rows, narrow
#     column projection, never full ORM hydration) - a per-partition
#     GROUP BY to get an "exact" breakdown was tried and measured multiple
#     seconds once a single partition held tens of thousands of rows, which
#     defeated the point. The two safety-relevant reasons
#     (provider_unavailable/resolver_error, see resolver.py:123,170) are
#     the one exception: a targeted indexed existence probe (LIMIT 1) per
#     partition guarantees those are never missed regardless of sampling,
#     since provider health must never depend on what got sampled.
# Nothing here changes what a prediction resolves to; this only changes how
# the health summary is computed.
_UNCLASSIFIED_SAMPLE_LIMIT = 2000
_RESOLVER_HEALTH_TIME_BUDGET_SECONDS = 2.5


def _overdue_partitions(db, due_base, naive_now):
    """One filtered query per canonical timeframe, each comparing
    resolution_deadline against a CONSTANT per-timeframe grace threshold
    (TIMEFRAME_SECONDS.get(timeframe, 300), same window the old per-row
    heuristic used). A single query with a CASE expression for this was
    measured 3x slower at 100k+ rows (EXPLAIN QUERY PLAN showed SQLite
    couldn't use the timeframe/resolution_deadline indexes at all once the
    comparison bound varied per row); a constant bound per partition lets
    each one seek its own index range directly.

    The catch-all partition for a non-canonical/legacy timeframe value is
    only added when one might actually exist - a `timeframe NOT IN (...)`
    condition can't use the timeframe index for a range/equality seek the
    way the canonical partitions above can (measured 1.3s on its own at
    100k+ rows, more than every canonical partition combined), so it's
    gated behind a cheap LIMIT-1 existence probe that costs ~0.1s and is
    almost always negative in practice (every write path canonicalizes the
    timeframe via app.timeframes.canonical.parse_timeframe)."""
    parts=[]
    for tf in TIMEFRAMES:
        threshold=naive_now-timedelta(seconds=TIMEFRAME_SECONDS.get(tf,300))
        parts.append(due_base.filter(PredictionLedger.timeframe==tf,PredictionLedger.resolution_deadline<=threshold))
    has_legacy_timeframe=db.query(PredictionLedger.timeframe).filter(~PredictionLedger.timeframe.in_(TIMEFRAMES)).limit(1).first() is not None
    if has_legacy_timeframe:
        catch_all_threshold=naive_now-timedelta(seconds=300)
        parts.append(due_base.filter(~PredictionLedger.timeframe.in_(TIMEFRAMES),PredictionLedger.resolution_deadline<=catch_all_threshold))
    return parts


@router.get("/resolver-health")
def resolver_health():
    """Phase 33 global resolver health - due/overdue counts, throughput,
    oldest overdue age, and the resolver scheduler's own last-cycle state.
    Distinct from prediction-resolution-summary's per-timeframe breakdown:
    this is the one aggregate view of "is the resolver keeping up right
    now", independent of any symbol/timeframe filter."""
    started = time.monotonic()
    db=SessionLocal()
    try:
        now=datetime.now(timezone.utc); naive_now=_naive_utc_now()
        scheduler_status=resolver_scheduler.status()
        resolver_running=bool(scheduler_status.get("running"))
        base=db.query(PredictionLedger).outerjoin(PredictionResolution,PredictionResolution.prediction_id==PredictionLedger.prediction_id).filter(PredictionResolution.id.is_(None))
        due_base=base.filter(PredictionLedger.resolution_deadline<=naive_now)
        due_count=due_base.count()
        not_due_count=base.filter(PredictionLedger.resolution_deadline>naive_now).count()
        # Overdue = due AND at least one grace period (its own horizon-bar
        # width) has elapsed since the deadline - distinct from "just became
        # due this instant", which resolves within the next cycle normally.
        # Partitioned per timeframe (see _overdue_partitions) so every
        # comparison bound is a constant SQLite can index-seek on, instead
        # of one query with a per-row CASE expression (measured 3x slower
        # at 100k+ rows: EXPLAIN QUERY PLAN showed the CASE form falls back
        # to a full scan, unable to use either the timeframe or
        # resolution_deadline index).
        # Exactly ONE cheap COUNT+MIN query per partition (11 partitions: 10
        # canonical timeframes + 1 catch-all) for the counts that must stay
        # exact no matter the backlog size - no GROUP BY here at all, so
        # this stays fast even at 100k+ rows (a per-partition GROUP BY was
        # measured taking multiple seconds once a partition held tens of
        # thousands of rows).
        overdue_count=0; oldest_overdue=None; overdue_parts=[]
        for part_q in _overdue_partitions(db,due_base,naive_now):
            part_count,part_oldest=part_q.with_entities(func.count(),func.min(PredictionLedger.resolution_deadline)).one()
            if not part_count: continue
            overdue_count+=part_count
            if part_oldest is not None and (oldest_overdue is None or part_oldest<oldest_overdue): oldest_overdue=part_oldest
            overdue_parts.append((part_q,part_count))

        # provider_unavailable/resolver_error are safety signals (backfill
        # provider health) and must never depend on sampling - a targeted,
        # indexed existence probe per partition (LIMIT 1) is exact and cheap
        # regardless of backlog size, unlike a full GROUP BY.
        provider_flagged=set()
        for part_q,_ in overdue_parts:
            hit=(part_q.filter(PredictionLedger.unresolved_reason.in_(("provider_unavailable","resolver_error")))
                 .with_entities(PredictionLedger.unresolved_reason).distinct().limit(2).all())
            provider_flagged.update(r for (r,) in hit)

        # Informational reason breakdown: a single bounded, clearly-labeled
        # sample across the overdue set (never unbounded, never a per-row
        # Python scan of the whole backlog) - pulled from each partition in
        # turn, stopping at the sample bound or the time budget.
        reason_counts=Counter(); sample_size=0; candle_latest_ms=None
        for part_q,part_count in overdue_parts:
            if sample_size>=_UNCLASSIFIED_SAMPLE_LIMIT or (time.monotonic()-started)>=_RESOLVER_HEALTH_TIME_BUDGET_SECONDS:
                break
            remaining_budget=_UNCLASSIFIED_SAMPLE_LIMIT-sample_size
            sample_rows=(part_q.with_entities(
                PredictionLedger.symbol,PredictionLedger.timeframe,PredictionLedger.resolution_deadline,
                PredictionLedger.reference_price,PredictionLedger.unresolved_reason,
            ).limit(remaining_budget).all())
            if not sample_rows: continue
            for symbol,timeframe,deadline,reference_price,persisted_reason in sample_rows:
                if persisted_reason:
                    reason_counts[persisted_reason]+=1
                else:
                    if candle_latest_ms is None:
                        candle_latest_ms={(s,tf):ms for s,tf,ms in db.query(MarketCandle.symbol,MarketCandle.timeframe,func.max(MarketCandle.timestamp)).group_by(MarketCandle.symbol,MarketCandle.timeframe)}
                    stub=SimpleNamespace(symbol=symbol,timeframe=timeframe,resolution_deadline=deadline,reference_price=reference_price,unresolved_reason=None)
                    reason_counts[_unresolved_reason(stub,naive_now,candle_latest_ms,resolver_running)]+=1
            sample_size+=len(sample_rows)
        exact_result=sample_size>=overdue_count
        # Never silently drop rows this endpoint couldn't classify in time -
        # whatever the bounded sample didn't reach is reported as its own
        # explicit bucket rather than vanishing from the total.
        not_sampled=overdue_count-sample_size
        if not_sampled>0:
            reason_counts["not_yet_sampled"]=not_sampled
        # The exact probe above guarantees these two are counted even when
        # they fall outside the informational sample window.
        for reason in provider_flagged:
            if reason not in reason_counts: reason_counts[reason]=1

        resolved_last_hour=db.query(PredictionResolution).filter(PredictionResolution.resolved_at>=now-timedelta(hours=1)).count()
        provider_error=None
        # A provider-facing reason among currently-overdue rows is the
        # clearest signal of provider health without a separate network
        # probe - never fabricate a status we haven't actually observed.
        # Both reasons are always exact (persisted, never in the sampled
        # bucket), so this check's accuracy is unaffected by sampling.
        if reason_counts.get("provider_unavailable"): provider_error="Backfill provider unreachable for one or more overdue predictions"
        elif reason_counts.get("resolver_error"): provider_error="Resolver raised an unexpected error during backfill"
        return {
            "resolver_running":resolver_running,
            "last_run":scheduler_status.get("last_run"),
            "last_batch_at":scheduler_status.get("last_batch_at"),
            "last_success_at":scheduler_status.get("last_success"),
            "next_run":scheduler_status.get("next_run"),
            "last_resolved_count":scheduler_status.get("last_resolved"),
            "current_error":scheduler_status.get("last_error"),
            "due_count":due_count,
            "not_due_count":not_due_count,
            "overdue_count":overdue_count,
            "resolved_last_hour":resolved_last_hour,
            "oldest_overdue_at":_iso(oldest_overdue),
            "oldest_overdue_age_seconds":round((naive_now-oldest_overdue).total_seconds()) if oldest_overdue else None,
            "unresolved_reason_counts":dict(reason_counts),
            # Phase 34: honesty metadata for the reason breakdown above -
            # exact only when the bounded sample covered every overdue row;
            # otherwise sample_size/overdue_count together say exactly how
            # much of the backlog the breakdown actually reflects. due_count/
            # not_due_count/overdue_count above are ALWAYS exact regardless -
            # only this per-reason breakdown is ever sampled.
            "unresolved_reason_counts_exact":exact_result,
            "unresolved_reason_sample_size":sample_size or None,
            "provider_status":"error" if provider_error else ("ok" if resolver_running else "unknown"),
            "provider_error":provider_error,
        }
    finally:db.close()


@router.get("/source-health")
def source_health(symbol:str=Query("BTCUSDT"),timeframe:str=Query("15m"),decision_id:str|None=None):
    try: timeframe=parse_timeframe(timeframe).value
    except ValueError as exc: raise HTTPException(422,{"code":"UNSUPPORTED_TIMEFRAME","message":"Unsupported timeframe."}) from exc
    symbol=symbol.upper(); db=SessionLocal()
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


# ===================================================== prediction cycles

class NewCycleRequest(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    idempotency_key: str | None = Field(default=None, max_length=80)
    symbol: str = "BTCUSDT"
    timeframe: str = "5m"


def _cycle_payload(cycle, evaluation=None):
    from app.trading import scheduler as trading_scheduler
    next_cycle = None
    if trading_scheduler.LAST_CYCLE_AT:
        try:
            next_cycle = (datetime.fromisoformat(trading_scheduler.LAST_CYCLE_AT)
                          + timedelta(seconds=settings.scheduler_interval_seconds)).isoformat()
        except ValueError:
            next_cycle = None
    return {"cycle_id": cycle.id if cycle else None, "label": cycle.label if cycle else None,
            "started_at": _iso(cycle.started_at) if cycle else None,
            "status": "active" if cycle else "no_cycle_started",
            "scheduler_running": trading_scheduler.RUNNING,
            "next_scheduled_prediction_at": next_cycle,
            "evaluation": evaluation}


@router.get("/prediction-cycle")
def prediction_cycle_status(current_user: str = Depends(get_current_user)):
    db = SessionLocal()
    try:
        cycle = db.query(PredictionCycle).filter(PredictionCycle.user_id == owner(current_user)).order_by(
            PredictionCycle.started_at.desc()).first()
        return _cycle_payload(cycle)
    finally:
        db.close()


@router.post("/prediction-cycle")
async def start_prediction_cycle(body: NewCycleRequest, current_user: str = Depends(get_current_user)):
    """Start a new prediction cycle. Non-destructive by design: history is
    never deleted, resolved outcomes are never rewritten, balances/model
    performance/risk limits are untouched, and no order is placed - the one
    evaluation triggered here flows through the exact same confidence, edge,
    authority, and risk gates as any scheduled evaluation. Auth is the JWT
    bearer dependency (no cookies, so CSRF does not apply); idempotency via
    the client key plus a 60s server-side re-trigger guard."""
    user_id = owner(current_user)
    db = SessionLocal()
    try:
        if body.idempotency_key:
            existing = db.query(PredictionCycle).filter(
                PredictionCycle.idempotency_key == body.idempotency_key).first()
            if existing:
                return {**_cycle_payload(existing), "created": False}
        latest = db.query(PredictionCycle).filter(PredictionCycle.user_id == user_id).order_by(
            PredictionCycle.started_at.desc()).first()
        now = datetime.now(timezone.utc)
        if latest and latest.started_at and (now.replace(tzinfo=None)
                - (latest.started_at.replace(tzinfo=None) if latest.started_at.tzinfo else latest.started_at)).total_seconds() < 60:
            raise HTTPException(429, "A prediction cycle was started less than a minute ago")
        cycle = PredictionCycle(id=uuid.uuid4().hex, user_id=user_id, label=body.label,
                                idempotency_key=body.idempotency_key, started_at=now)
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
    finally:
        db.close()
    evaluation = None
    try:
        timeframe = parse_timeframe(body.timeframe).value
        from app.api.prediction import prediction as active_drive_prediction
        result = await active_drive_prediction(body.symbol.upper(), timeframe=timeframe, current_user=current_user)
        engine_result = result.get("decision_engine") or {}
        evaluation = {"symbol": body.symbol.upper(), "timeframe": timeframe,
                      "signal": engine_result.get("final_signal"),
                      "decision_id": engine_result.get("decision_id"),
                      "blocking_reasons": engine_result.get("blocking_reasons")}
    except Exception as exc:  # evaluation failure must not lose the cycle row
        evaluation = {"error": repr(exc)}
    return {**_cycle_payload(cycle, evaluation), "created": True}
