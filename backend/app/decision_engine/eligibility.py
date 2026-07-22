"""Per-indicator eligibility lookup (Bot Settings Part 4/9).

Absence of an IndicatorEligibility row means ACTIVE - this module only ever
records exceptions to the default, so a fresh install with no eligibility
rows behaves identically to today's all-active behavior. Scoped by `mode`
("paper" | "binance_real") so removing an indicator's influence from one
mode never touches the other, and NOT scoped by market regime (regime is
tracked for performance diagnostics only - see IndicatorPerformanceRollup).

evaluate() in v2.py calls batch_lookup() once per decision cycle (a single
query for every candidate identity about to be built) rather than one query
per candidate, to avoid N+1 queries alongside the existing per-candidate
performance() call.
"""
from __future__ import annotations

from app.db.models import IndicatorEligibility

STATUS_ACTIVE = "ACTIVE"
STATUS_SHADOW_ONLY_POOR_PERFORMANCE = "SHADOW_ONLY_POOR_PERFORMANCE"
STATUS_MANUALLY_DISABLED = "MANUALLY_DISABLED"
STATUS_RECOMMENDED_FOR_REACTIVATION = "RECOMMENDED_FOR_REACTIVATION"
STATUS_INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
STATUS_DATA_QUALITY_BLOCKED = "DATA_QUALITY_BLOCKED"

# Statuses under which a candidate still computes but is excluded from the
# active ensemble sum (Part 5/9 - "shadow", for scoring purposes).
# STATUS_INSUFFICIENT_SAMPLE is deliberately NOT here: it means "not enough
# resolved history to judge this indicator yet," not "proven poor" - a
# brand-new indicator must keep contributing normally (execution_mode
# ACTIVE) until it is actually shown to be a poor performer (7+/10 wrong),
# never excluded merely for being new. It still exists as a distinct,
# displayed status (Part 7's "Insufficient Sample" filter) - just not one
# that changes scoring behavior.
SHADOW_STATUSES = (STATUS_SHADOW_ONLY_POOR_PERFORMANCE, STATUS_RECOMMENDED_FOR_REACTIVATION,
                    STATUS_DATA_QUALITY_BLOCKED)


def execution_mode_for_status(status: str) -> str:
    """Maps an IndicatorEligibility.status to the per-candidate
    execution_mode used by v2.py/ledger.py: ACTIVE | SHADOW | DISABLED."""
    if status == STATUS_MANUALLY_DISABLED:
        return "DISABLED"
    if status in SHADOW_STATUSES:
        return "SHADOW"
    return "ACTIVE"


def lookup(db, source_name: str, source_version: str, symbol: str, timeframe: str, mode: str) -> dict:
    """Single-identity lookup. Defaults to ACTIVE with no persisted row."""
    row = (
        db.query(IndicatorEligibility)
        .filter_by(source_name=source_name, source_version=source_version, symbol=symbol,
                   timeframe=timeframe, mode=mode)
        .first()
    )
    if row is None:
        return {"status": STATUS_ACTIVE, "reason": None}
    return {"status": row.status, "reason": row.status_reason}


def batch_lookup(db, identities: list, mode: str) -> dict:
    """identities: list of (source_name, source_version, symbol, timeframe)
    tuples. Returns {identity_tuple: {"status":..., "reason":...}}, ACTIVE
    for any identity with no persisted row. One query for the whole batch."""
    if not identities:
        return {}
    source_names = {i[0] for i in identities}
    rows = (
        db.query(IndicatorEligibility)
        .filter(IndicatorEligibility.source_name.in_(source_names), IndicatorEligibility.mode == mode)
        .all()
    )
    by_identity = {
        (row.source_name, row.source_version, row.symbol, row.timeframe): {"status": row.status, "reason": row.status_reason}
        for row in rows
    }
    return {
        identity: by_identity.get(identity, {"status": STATUS_ACTIVE, "reason": None})
        for identity in identities
    }
