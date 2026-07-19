from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.core.deps import get_current_user
from app.db.session import get_db
from app.db.models import ActiveDriveDecision, DecisionEngineChange, PredictionLedger, PredictionResolution
from app.decision_engine.cache import invalidate_user
from app.decision_engine.repository import get_setting, is_available, set_engine
from app.decision_engine.router import decision_engine_router
from app.decision_engine.types import DecisionEngineType
from app.trading import modes

router = APIRouter(prefix="/api/bot", tags=["bot"])

BOT_STATE = {
    "status": "running",
    "mode": "paper",
    "live_trading_enabled": False,
    "last_action": "init",
    "updated_at": datetime.now(timezone.utc).isoformat(),
}

def update_state(action: str):
    BOT_STATE["last_action"] = action
    BOT_STATE["updated_at"] = datetime.now(timezone.utc).isoformat()

@router.get("/status")
async def bot_status(db: Session = Depends(get_db)):
    control = modes.get_control(db)
    return {**BOT_STATE, "status": control["execution_state"], "mode": modes.effective_mode(db).lower(),
            "live_trading_enabled": modes.effective_mode(db) == modes.MODE_LIVE}

@router.post("/start")
async def start_bot(db: Session = Depends(get_db)):
    modes.set_execution_state("running", db=db)
    BOT_STATE["status"] = "running"
    update_state("start")
    return {"ok": True, "message": "Bot started", "state": BOT_STATE}

@router.post("/pause")
async def pause_bot(db: Session = Depends(get_db)):
    modes.set_execution_state("paused", db=db)
    BOT_STATE["status"] = "paused"
    update_state("pause")
    return {"ok": True, "message": "Bot paused", "state": BOT_STATE}

@router.post("/stop")
async def stop_bot(db: Session = Depends(get_db)):
    modes.set_execution_state("stopped", db=db)
    BOT_STATE["status"] = "stopped"
    update_state("stop")
    return {"ok": True, "message": "Bot stopped", "state": BOT_STATE}

@router.post("/paper")
async def paper_mode(db: Session = Depends(get_db)):
    modes.set_mode(modes.MODE_PAPER, db=db)
    BOT_STATE["mode"] = "paper"
    update_state("paper")
    return {"ok": True, "message": "Paper mode enabled", "state": BOT_STATE}

@router.post("/live")
async def live_mode():
    BOT_STATE["mode"] = "paper"
    BOT_STATE["live_trading_enabled"] = False
    update_state("live_blocked")
    return {
        "ok": False,
        "message": "Live mode is locked until API keys, risk limits, and execution safeguards are configured.",
        "state": BOT_STATE,
    }

class EngineSwitchRequest(BaseModel):
    engine: DecisionEngineType
    acknowledged: bool = False
    reason: str | None = None

class ShadowCompareRequest(BaseModel):
    enabled: bool


def _engine_state(db: Session, user_id: str) -> dict:
    setting = get_setting(db, user_id)
    latest = db.query(ActiveDriveDecision).filter(ActiveDriveDecision.user_id == setting.user_id,
        ActiveDriveDecision.shadow.is_(False)).order_by(ActiveDriveDecision.created_at.desc()).first()
    last_change = db.query(DecisionEngineChange).filter(DecisionEngineChange.user_id == setting.user_id).order_by(DecisionEngineChange.created_at.desc()).first()
    resolved_count = db.query(PredictionResolution).join(PredictionLedger,
        PredictionResolution.prediction_id == PredictionLedger.prediction_id).filter(PredictionLedger.user_id == setting.user_id).count()
    available = []
    for engine_id, engine in decision_engine_router.engines.items():
        available.append({"id": engine_id.value, "name": "Active Drive V2" if engine_id == DecisionEngineType.ACTIVE_DRIVE_V2 else "Active Drive V1",
            "version": engine.version, "available": is_available(engine_id), "selected": setting.decision_engine == engine_id.value,
            "health": engine.health()["status"], "capabilities": engine.capabilities(),
            "legacy": engine_id == DecisionEngineType.ACTIVE_DRIVE_V1})
    return {"active_engine": setting.decision_engine, "available_engines": available, "automatic_fallback": False,
        "compare_engines_shadow": setting.compare_engines_shadow, "last_decision": latest.decision_payload if latest else None,
        "last_decision_time": latest.created_at.isoformat() if latest else None, "resolved_history_count": resolved_count,
        "last_switch": ({"previous_engine": last_change.previous_engine, "new_engine": last_change.new_engine,
            "changed_by": last_change.changed_by, "created_at": last_change.created_at.isoformat()} if last_change else None)}

@router.get("/decision-engine")
def get_decision_engine(current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    return _engine_state(db, current_user)

@router.patch("/decision-engine")
def patch_decision_engine(body: EngineSwitchRequest, current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    if not is_available(body.engine):
        raise HTTPException(status_code=409, detail="Selected decision engine is not available")
    if not body.acknowledged:
        raise HTTPException(status_code=400, detail="Engine switch acknowledgement is required")
    set_engine(db, current_user, body.engine, changed_by=current_user, reason=body.reason)
    invalidate_user(current_user)
    return _engine_state(db, current_user)

@router.patch("/decision-engine/comparison")
def patch_engine_comparison(body: ShadowCompareRequest, current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    setting = get_setting(db, current_user)
    setting.compare_engines_shadow = body.enabled
    db.commit()
    return _engine_state(db, current_user)

@router.get("/decision-engine/comparison")
def engine_comparison(current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    setting = get_setting(db, current_user)
    rows = db.query(ActiveDriveDecision).filter(ActiveDriveDecision.user_id == setting.user_id).order_by(ActiveDriveDecision.created_at.desc()).limit(20).all()
    return {"enabled": setting.compare_engines_shadow, "decisions": [{"decision_id": r.decision_id, "engine": r.engine,
        "engine_version": r.engine_version, "symbol": r.symbol, "timeframe": r.timeframe, "signal": r.signal,
        "confidence": r.confidence, "long_points": r.long_points, "short_points": r.short_points,
        "shadow": r.shadow, "created_at": r.created_at.isoformat()} for r in rows]}
