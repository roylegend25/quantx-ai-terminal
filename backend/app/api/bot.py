from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.deps import get_current_user
from app.db.session import get_db
from app.db.models import ActiveDriveDecision, DecisionEngineChange, PredictionLedger, PredictionResolution, Trade
from app.decision_engine.cache import invalidate_user
from app.decision_engine.repository import get_setting, is_available, set_engine
from app.decision_engine.router import decision_engine_router
from app.decision_engine.types import DecisionEngineType
from app.deployment import maintenance
from app.deployment.lease import execution_lease
from app.core.config import settings
from app.trading import scheduler as trading_scheduler

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

def _effective_status() -> str:
    """BOT_STATE alone used to be the entire answer, and it started at
    "running" the instant this module was imported - before the scheduler's
    startup grace period even elapsed, and with no link at all to whether
    the asyncio loop in app.trading.scheduler was actually alive. A real
    trade can now actually be placed once current-edge support is wired up,
    so this can no longer just be a decorative label: "running" is only
    reported when the scheduler loop is actually iterating and not
    deployment-maintenance-blocked."""
    if trading_scheduler.RUNNING:
        return "paused" if maintenance.enabled() else "running"
    return BOT_STATE["status"] if BOT_STATE["status"] in ("paused", "stopped") else "stopped"

def _next_cycle_estimate(last_cycle_iso: str | None) -> str | None:
    if not last_cycle_iso:
        return None
    try:
        last = datetime.fromisoformat(last_cycle_iso)
    except ValueError:
        return None
    return (last + timedelta(seconds=settings.scheduler_interval_seconds)).isoformat()

def _automated_trade_fields(db: Session | None) -> dict:
    """Best-effort automated-trade observability - never blocks status on a
    query failure, since /api/bot/status must stay usable even if the DB is
    briefly unreachable."""
    if db is None:
        return {"open_automatic_positions": None, "last_automatic_trade_id": None, "last_automatic_trade_at": None}
    try:
        open_automatic = db.query(Trade).filter(Trade.status == "OPEN", Trade.execution_mode == "automatic").count()
        latest = (db.query(Trade).filter(Trade.execution_mode == "automatic")
                  .order_by(Trade.opened_at.desc()).first())
        return {"open_automatic_positions": open_automatic,
                "last_automatic_trade_id": latest.id if latest else None,
                "last_automatic_trade_at": latest.opened_at.isoformat() if latest and latest.opened_at else None}
    except Exception:
        return {"open_automatic_positions": None, "last_automatic_trade_id": None, "last_automatic_trade_at": None}

def _status_payload(db: Session | None = None) -> dict:
    return {**BOT_STATE, "status": _effective_status(),
            "scheduler_running": trading_scheduler.RUNNING,
            "maintenance_mode": maintenance.enabled(),
            "execution_lease_held": execution_lease.held,
            "effective_trading_active": trading_scheduler.RUNNING and not maintenance.enabled(),
            "scheduler_last_cycle": trading_scheduler.LAST_CYCLE_AT,
            "scheduler_next_cycle": _next_cycle_estimate(trading_scheduler.LAST_CYCLE_AT),
            **_automated_trade_fields(db)}

@router.get("/status")
async def bot_status(db: Session = Depends(get_db)):
    return _status_payload(db)

@router.post("/start")
async def start_bot(db: Session = Depends(get_db)):
    trading_scheduler.start_scheduler()
    BOT_STATE["status"] = "running"
    update_state("start")
    return {"ok": True, "message": "Bot started", "state": _status_payload(db)}

@router.post("/pause")
async def pause_bot(db: Session = Depends(get_db)):
    trading_scheduler.stop_scheduler()
    BOT_STATE["status"] = "paused"
    update_state("pause")
    return {"ok": True, "message": "Bot paused", "state": _status_payload(db)}

@router.post("/stop")
async def stop_bot(db: Session = Depends(get_db)):
    trading_scheduler.stop_scheduler()
    BOT_STATE["status"] = "stopped"
    update_state("stop")
    return {"ok": True, "message": "Bot stopped", "state": _status_payload(db)}

@router.post("/paper")
async def paper_mode():
    BOT_STATE["mode"] = "paper"
    update_state("paper")
    return {"ok": True, "message": "Paper mode enabled", "state": _status_payload()}

@router.post("/live")
async def live_mode():
    BOT_STATE["mode"] = "paper"
    BOT_STATE["live_trading_enabled"] = False
    update_state("live_blocked")
    return {
        "ok": False,
        "message": "Live mode is locked until API keys, risk limits, and execution safeguards are configured.",
        "state": _status_payload(),
    }

class EngineSwitchRequest(BaseModel):
    engine: DecisionEngineType
    acknowledged: bool = False
    reason: str | None = None

class ShadowCompareRequest(BaseModel):
    enabled: bool


TRADING_PROFILES = {"auto_adaptive", "short_term", "mid_term", "safe"}


class TradingProfilePatch(BaseModel):
    trading_profile: str
    strict_timeframe_unanimity: bool = True
    auto_profile_enabled: bool = True
    expected_revision: int = Field(ge=1)


def _profile_state(setting) -> dict:
    return {
        "trading_profile": setting.trading_profile or "auto_adaptive",
        "strict_timeframe_unanimity": setting.strict_timeframe_unanimity is not False,
        "strict_unanimity_required": True,
        "auto_profile_enabled": setting.auto_profile_enabled is not False,
        "profile_updated_at": setting.profile_updated_at.isoformat() if setting.profile_updated_at else None,
        "profile_revision": setting.profile_revision or 1,
        "persistence_available": True,
        "authority_source": "server",
    }


@router.get("/trading-profile")
def get_trading_profile(current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    return _profile_state(get_setting(db, current_user))


@router.patch("/trading-profile")
def patch_trading_profile(body: TradingProfilePatch, current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.trading_profile not in TRADING_PROFILES:
        raise HTTPException(status_code=422, detail="Invalid trading_profile")
    if body.trading_profile == "auto_adaptive" and not body.auto_profile_enabled:
        raise HTTPException(status_code=422, detail="auto_profile_enabled is required for auto_adaptive")
    if body.strict_timeframe_unanimity is not True:
        raise HTTPException(status_code=422, detail="STRICT_TIMEFRAME_UNANIMITY_REQUIRED")
    setting = get_setting(db, current_user)
    revision = setting.profile_revision or 1
    if revision != body.expected_revision:
        raise HTTPException(status_code=409, detail="PROFILE_REVISION_CONFLICT")
    previous = _profile_state(setting)
    setting.trading_profile = body.trading_profile
    setting.strict_timeframe_unanimity = True
    setting.auto_profile_enabled = body.auto_profile_enabled
    setting.profile_updated_at = datetime.now(timezone.utc)
    setting.profile_revision = revision + 1
    db.commit()
    db.refresh(setting)
    invalidate_user(setting.user_id)
    from app.trading import modes
    modes.audit("trading_profile_changed", detail={"user_id": setting.user_id, "previous": previous,
                "current": _profile_state(setting), "affects": "future_entries_only"})
    return _profile_state(setting)


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
