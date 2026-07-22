"""Resolve the one authoritative execution timeframe for a symbol right
now.

Trading Horizon removal: this no longer reads TradingHorizonDecision or
falls back to a live multi-timeframe preview evaluation (the mechanism
that produced the ~85s, 6-timeframe re-evaluation cascade). Resolution is
now a pure, static configuration lookup - the user's configured trading
profile's execution timeframe - with no live evaluation, no Horizon
dependency, and therefore no possibility of a GET request triggering a
fresh V2 computation."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.decision_engine.profiles import resolve_execution_timeframe
from app.decision_engine.repository import get_setting


async def resolve_authoritative_timeframe(db: Session, user_id: str, symbol: str) -> dict:
    symbol = symbol.upper()
    setting = get_setting(db, user_id)
    profile_name = setting.trading_profile or None
    execution_tf = resolve_execution_timeframe(profile_name)
    return {
        "execution_timeframe": execution_tf,
        "source": "configured_profile",
        "decision_id": None,
        "expires_at": None,
    }
