from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.models import UserBotSetting, DecisionEngineChange, PredictionLedger, PredictionResolution
from app.decision_engine.types import DecisionEngineType

DEFAULT_ENGINE = DecisionEngineType(settings.default_decision_engine)

def is_available(engine: DecisionEngineType) -> bool:
    return settings.active_drive_v2_enabled if engine == DecisionEngineType.ACTIVE_DRIVE_V2 else settings.active_drive_v1_available

def owner(user_id: str) -> str:
    return settings.admin_username if user_id == "internal-scheduler" else user_id

def get_setting(db: Session, user_id: str) -> UserBotSetting:
    user_id = owner(user_id)
    row = db.get(UserBotSetting, user_id)
    if row is None:
        row = UserBotSetting(user_id=user_id, decision_engine=DEFAULT_ENGINE.value, compare_engines_shadow=False)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row

def set_engine(db: Session, user_id: str, engine: DecisionEngineType, changed_by: str, reason: str | None = None) -> UserBotSetting:
    row = get_setting(db, user_id)
    previous = row.decision_engine
    if previous != engine.value:
        row.decision_engine = engine.value
        row.updated_at = datetime.now(timezone.utc)
        db.add(DecisionEngineChange(user_id=row.user_id, previous_engine=previous, new_engine=engine.value,
                                    changed_by=changed_by, reason=reason or "Authenticated manual engine switch"))
        db.commit()
        db.refresh(row)
    return row

def performance(db: Session, source_name: str, source_version: str, symbol: str, timeframe: str, regime: str | None) -> dict:
    q = db.query(PredictionResolution).join(PredictionLedger, PredictionResolution.prediction_id == PredictionLedger.prediction_id).filter(
        PredictionLedger.source_name == source_name,
        PredictionLedger.source_version == source_version,
        PredictionLedger.symbol == symbol,
        PredictionLedger.timeframe == timeframe,
    )
    if regime:
        q = q.filter(PredictionLedger.market_regime == regime)
    rows = q.order_by(PredictionResolution.resolved_at.desc()).limit(100).all()
    n = len(rows)
    wins = sum(1 for row in rows if row.correct is True)
    accuracy = wins / n if n else None
    posterior = (wins + 10.0) / (n + 20.0)
    recent = rows[:20]
    recent_wins = sum(1 for row in recent if row.correct is True)
    recent_posterior = (recent_wins + 10.0) / (len(recent) + 20.0)
    realized = [float(row.actual_return) for row in rows if row.actual_return is not None]
    realized_edge = sum(realized) / len(realized) if realized else None
    tier = "trusted" if n >= 100 else "eligible" if n >= 50 else "early_evidence" if n >= 20 else "insufficient_evidence"
    return {"resolved": n, "accuracy": accuracy,
            "recent_accuracy": recent_wins / len(recent) if recent else None,
            "shrunk_accuracy": posterior, "recent_shrunk_accuracy": recent_posterior,
            "realized_edge": realized_edge, "tier": tier}
