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

def _signed_return(actual_return: float | None, direction: str | None) -> float | None:
    """actual_return from resolver.py is the raw, unsigned
    (close - reference_price) / reference_price - a correct SHORT (price
    dropped) has a *negative* actual_return even though it was a win. Every
    caller that mixes LONG/SHORT history (realized_edge, average_win/loss
    return) needs the direction-adjusted value instead, or SHORT wins read
    as losses and vice versa."""
    if actual_return is None:
        return None
    if direction == "SHORT":
        return -float(actual_return)
    if direction == "LONG":
        return float(actual_return)
    return None


def performance(db: Session, user_id: str, source_name: str, source_version: str, symbol: str, timeframe: str, regime: str | None) -> dict:
    q = db.query(PredictionResolution, PredictionLedger.direction).join(
        PredictionLedger, PredictionResolution.prediction_id == PredictionLedger.prediction_id
    ).filter(
        PredictionLedger.user_id == owner(user_id),
        PredictionLedger.engine == "active_drive_v2",
        PredictionLedger.source_name == source_name,
        PredictionLedger.source_version == source_version,
        PredictionLedger.symbol == symbol,
        PredictionLedger.timeframe == timeframe,
    )
    if regime:
        q = q.filter(PredictionLedger.market_regime == regime)
    rows = q.order_by(PredictionResolution.resolved_at.desc()).limit(100).all()
    n = len(rows)
    wins = sum(1 for row, _ in rows if row.correct is True)
    directional = [(row, direction) for row, direction in rows if row.correct is not None]
    directional_n = len(directional)
    accuracy = wins / directional_n if directional_n else None
    posterior = (wins + 10.0) / (directional_n + 20.0)
    recent = rows[:20]
    recent_wins = sum(1 for row, _ in recent if row.correct is True)
    recent_directional = [(row, direction) for row, direction in recent if row.correct is not None]
    recent_posterior = (recent_wins + 10.0) / (len(recent_directional) + 20.0)
    realized = [r for r in (_signed_return(row.actual_return, direction) for row, direction in rows) if r is not None]
    realized_edge = sum(realized) / len(realized) if realized else None
    winning_returns = [r for r in (_signed_return(row.actual_return, direction) for row, direction in rows if row.correct is True) if r is not None]
    losing_returns = [r for r in (_signed_return(row.actual_return, direction) for row, direction in rows if row.correct is False) if r is not None]
    tier = "trusted" if n >= 100 else "eligible" if n >= 50 else "early_evidence" if n >= 20 else "insufficient_evidence"
    latest_resolved_at = rows[0][0].resolved_at if rows and rows[0][0].resolved_at else None
    return {"resolved": n, "accuracy": accuracy,
            "recent_accuracy": recent_wins / len(recent_directional) if recent_directional else None,
            "shrunk_accuracy": posterior, "recent_shrunk_accuracy": recent_posterior,
            "realized_edge": realized_edge, "tier": tier, "directional_resolved": directional_n,
            "neutral_resolved": n - directional_n,
            "average_win_return": sum(winning_returns)/len(winning_returns) if winning_returns else None,
            "average_loss_return": sum(losing_returns)/len(losing_returns) if losing_returns else None,
            "resolved_at_latest": latest_resolved_at.isoformat() if latest_resolved_at else None}
