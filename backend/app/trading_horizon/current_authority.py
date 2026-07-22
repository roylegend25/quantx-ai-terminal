"""Resolve the one authoritative execution timeframe for a symbol right now.

This is the single fix for the Decision/Execution Pipeline contradiction:
every consumer that needs "what timeframe would the scheduler actually
trade" (the Binance Live decision-status card, the paper execution
pipeline card, the new unified pipeline API) must call this instead of
picking their own timeframe (the dashboard's sticky chart interval) or
hardcoding one (the former literal "5m").

Resolution order, most authoritative first:
  1. The latest non-expired, persisted TradingHorizonDecision for this
     (user, symbol) - this is literally the timeframe the scheduler has
     already committed to for the live entry window.
  2. A side-effect-free Trading Horizon preview (the same profile/
     multi-timeframe resolution logic the scheduler itself uses,
     `_evaluate_horizon(..., issue_authority=False)`) - "what would be
     issued right now" when nothing has been issued yet.
  3. The user's explicitly configured (non-auto) trading profile.
  4. The default profile's execution timeframe, only if every prior step
     was unavailable (e.g. Active Drive V2 not selected).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import TradingHorizonDecision
from app.decision_engine.repository import get_setting, owner
from app.trading_horizon.service import PROFILES

DEFAULT_PROFILE = "short_term"


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def resolve_authoritative_timeframe(db: Session, user_id: str, symbol: str) -> dict:
    symbol = symbol.upper()
    normalized_user = owner(user_id)
    now = datetime.now(timezone.utc)

    row = (
        db.query(TradingHorizonDecision)
        .filter(TradingHorizonDecision.user_id == normalized_user, TradingHorizonDecision.symbol == symbol)
        .order_by(TradingHorizonDecision.generated_at.desc())
        .first()
    )
    if row is not None and _aware(row.expires_at) > now:
        return {
            "execution_timeframe": row.authoritative_execution_timeframe,
            "source": "persisted_authority",
            "horizon_decision_id": row.id,
            "expires_at": row.expires_at.isoformat(),
        }

    try:
        from app.api.timeframes import _evaluate_horizon
        preview = await _evaluate_horizon(
            symbol, current_user=user_id, issue_authority=False,
            evaluation_reason="pipeline_timeframe_resolution",
        )
        execution_tf = preview.get("execution_timeframe")
        if execution_tf:
            return {
                "execution_timeframe": execution_tf,
                "source": "preview_no_authority_yet",
                "horizon_decision_id": None,
                "expires_at": None,
            }
    except Exception:
        pass

    setting = get_setting(db, user_id)
    profile_name = setting.trading_profile or "auto_adaptive"
    if profile_name != "auto_adaptive" and profile_name in PROFILES:
        return {
            "execution_timeframe": PROFILES[profile_name].execution_timeframe,
            "source": "configured_profile",
            "horizon_decision_id": None,
            "expires_at": None,
        }

    return {
        "execution_timeframe": PROFILES[DEFAULT_PROFILE].execution_timeframe,
        "source": "default_profile",
        "horizon_decision_id": None,
        "expires_at": None,
    }
