"""Authenticated, read-only Active Drive source and resolver health."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Query
from sqlalchemy import func
from app.core.config import settings
from app.db.models import ActiveDriveDecision, MarketCandle, PredictionLedger, PredictionResolution, SignalCandidateRecord
from app.db.session import SessionLocal
from app.decision_engine import scheduler as resolver_scheduler
from app.decision_engine.v2 import SHADOW_MODELS
from app.quant.forecast import TIMEFRAME_SECONDS

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

def _iso(value): return value.isoformat() if value else None

@router.get("/source-health")
def source_health(symbol: str = Query("BTCUSDT"), timeframe: str = Query("15m")):
    symbol, timeframe = symbol.upper(), timeframe.lower(); db = SessionLocal()
    try:
        decision = db.query(ActiveDriveDecision).filter(ActiveDriveDecision.engine == "active_drive_v2", ActiveDriveDecision.symbol == symbol, ActiveDriveDecision.timeframe == timeframe).order_by(ActiveDriveDecision.created_at.desc()).first()
        rows = [] if decision is None else db.query(SignalCandidateRecord).filter(SignalCandidateRecord.decision_id == decision.decision_id).all()
        sources=[]
        for row in rows:
            shadow=row.source_type=="ml" and row.source_version=="shadow-1"
            missing=bool(row.rejection_reason and "unavailable" in row.rejection_reason.lower())
            runtime="shadow_not_inferred" if shadow else "unavailable_data" if missing else "working"
            sources.append({"source_type":row.source_type,"source_name":row.source_name,"version":row.source_version,"family":row.source_family,
                "configured_status":"shadow" if shadow else "enabled","runtime_status":runtime,"dependency_available":not missing,
                "production_eligible":bool(row.eligible and not shadow),"shadow":shadow,"last_successfully_evaluated_time":_iso(row.created_at),
                "last_error":row.rejection_reason,"supported_symbols":[symbol],"supported_timeframes":[timeframe],
                "supported_regimes":[row.market_regime] if row.market_regime else [],"direction":row.direction,"points":row.candidate_points,
                "resolved_samples":row.resolved_sample_size,"evidence_tier":row.evidence_tier,"fresh":row.data_freshness=="live"})
        known={s["source_name"] for s in sources}
        for name,family in SHADOW_MODELS:
            if name not in known:
                sources.append({"source_type":"ml","source_name":name,"version":"shadow-1","family":family,"configured_status":"shadow",
                    "runtime_status":"shadow_not_inferred","dependency_available":False,"production_eligible":False,"shadow":True,
                    "last_successfully_evaluated_time":None,"last_error":"No validated artifact/inference wired into V2","supported_symbols":[symbol],
                    "supported_timeframes":[timeframe],"supported_regimes":[],"direction":"NO_TRADE","points":0,"resolved_samples":0,
                    "evidence_tier":"insufficient_evidence","fresh":False})
        now=datetime.now(timezone.utc)
        base=db.query(PredictionLedger).filter(PredictionLedger.symbol==symbol,PredictionLedger.timeframe==timeframe)
        total=base.count(); resolved=base.join(PredictionResolution,PredictionResolution.prediction_id==PredictionLedger.prediction_id).count()
        outcome_grace = timedelta(seconds=TIMEFRAME_SECONDS.get(timeframe, 300))
        expired=base.outerjoin(PredictionResolution,PredictionResolution.prediction_id==PredictionLedger.prediction_id).filter(PredictionResolution.id.is_(None),PredictionLedger.resolution_deadline < now - outcome_grace).count()
        by_type={k:{"total":n,"resolved":done,"unresolved":n-done} for k,n,done in db.query(PredictionLedger.source_type,func.count(PredictionLedger.prediction_id),func.count(PredictionResolution.id)).outerjoin(PredictionResolution,PredictionResolution.prediction_id==PredictionLedger.prediction_id).filter(PredictionLedger.symbol==symbol,PredictionLedger.timeframe==timeframe).group_by(PredictionLedger.source_type).all()}
        candle_max=db.query(func.max(MarketCandle.timestamp)).filter(MarketCandle.symbol==symbol,MarketCandle.timeframe==timeframe).scalar()
        resolver=resolver_scheduler.status(); resolver.update({"healthy":bool(resolver.get("running") and not resolver.get("last_error") and expired==0),"expired_unresolved":expired,"market_candle_latest":candle_max,"degraded_reason":"Expired predictions lack stored outcome candles" if expired else None})
        types=Counter(s["source_type"] for s in sources); working=Counter(s["source_type"] for s in sources if s["runtime_status"]=="working"); payload=decision.decision_payload if decision else {}
        return {"summary":{"ml_total":types["ml"],"ml_working":working["ml"],"strategy_total":types["strategy"],"strategy_working":working["strategy"],"quant_total":types["quant"],"quant_working":working["quant"],"candidates_generated":len(rows),"ledger_writes":total,"resolver_healthy":resolver["healthy"]},
            "sources":sorted(sources,key=lambda x:(x["source_type"],x["source_name"])),"ledger":{"total":total,"resolved":resolved,"unresolved":total-resolved,"expired_unresolved":expired,"duplicates":0,"by_source_type":by_type},"resolver":resolver,
            "decision_requirements":{"decision_id":decision.decision_id if decision else None,"generated_at":_iso(decision.created_at) if decision else None,"signal":decision.signal if decision else None,"total_evidence":payload.get("total_evidence"),"minimum_total_evidence":payload.get("minimum_total_evidence",settings.active_drive_min_total_evidence),"point_margin":payload.get("point_margin"),"required_point_margin":payload.get("required_point_margin",settings.active_drive_min_point_margin),"directional_confidence":payload.get("directional_confidence"),"required_confidence":payload.get("required_confidence",settings.active_drive_min_confidence),"minimum_resolved_samples":settings.active_drive_min_resolved_samples,"blocking_reasons":decision.blocking_reasons if decision else []}}
    finally: db.close()
