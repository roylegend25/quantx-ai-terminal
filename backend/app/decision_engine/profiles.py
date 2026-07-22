"""Static execution-timeframe-per-profile configuration. Extracted from
Trading Horizon's TradingProfile (which also carried multi-timeframe
confirmation/unanimity fields no longer needed - the single-decision V2
model evaluates exactly one timeframe per cycle, with its own evidence/
confidence/margin/edge gates, rather than requiring cross-timeframe
unanimity)."""
from __future__ import annotations

EXECUTION_TIMEFRAMES: dict[str, str] = {
    "short_term": "5m",
    "mid_term": "15m",
    "safe": "1h",
}
DEFAULT_PROFILE = "short_term"


def resolve_execution_timeframe(profile_key: str | None) -> str:
    """auto_adaptive (the legacy default, which used to trigger a live
    multi-timeframe evaluation to pick a profile) now resolves to the
    static default profile - no live evaluation cascade."""
    if profile_key and profile_key in EXECUTION_TIMEFRAMES:
        return EXECUTION_TIMEFRAMES[profile_key]
    return EXECUTION_TIMEFRAMES[DEFAULT_PROFILE]
