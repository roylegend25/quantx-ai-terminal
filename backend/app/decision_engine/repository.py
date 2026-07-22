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
    """Resolved out-of-sample performance for one source's evidence bucket.

    Documented evidence hierarchy (no fabrication, fail-closed):
      1. source + version + symbol + timeframe + regime (preferred)
      2. source + version + symbol + timeframe (all regimes) - used only
         when the regime bucket alone cannot meet the minimum sample
         requirement. Regime labels fragment quickly (every volatility x
         trend combination is a separate string, plus legacy labels), so
         requiring 20 samples inside the CURRENT label made the history
         gate near-permanently 0/20 despite thousands of resolved samples
         for the same source/symbol/timeframe.
    The fallback still faces the full 20-sample gate, keeps the Bayesian
    shrinkage prior, and reports its scope; when even it has no rows the
    result honestly stays empty (never established).
    """
    def bucket(with_regime: bool):
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
        if with_regime and regime:
            q = q.filter(PredictionLedger.market_regime == regime)
        return q.order_by(PredictionResolution.resolved_at.desc()).limit(100).all()

    rows = bucket(with_regime=True)
    evidence_scope = "source_symbol_timeframe_regime" if regime else "source_symbol_timeframe"
    if regime and len(rows) < settings.active_drive_min_resolved_samples:
        fallback = bucket(with_regime=False)
        if len(fallback) > len(rows):
            rows, evidence_scope = fallback, "source_symbol_timeframe"
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
    return {"resolved": n, "accuracy": accuracy, "evidence_scope": evidence_scope,
            "recent_accuracy": recent_wins / len(recent_directional) if recent_directional else None,
            "shrunk_accuracy": posterior, "recent_shrunk_accuracy": recent_posterior,
            "realized_edge": realized_edge, "tier": tier, "directional_resolved": directional_n,
            "neutral_resolved": n - directional_n,
            "average_win_return": sum(winning_returns)/len(winning_returns) if winning_returns else None,
            "average_loss_return": sum(losing_returns)/len(losing_returns) if losing_returns else None,
            "resolved_at_latest": latest_resolved_at.isoformat() if latest_resolved_at else None}


def _performance_from_rows(rows: list, evidence_scope: str) -> dict:
    """Same bucket-shaped stats performance() computes from its own
    per-source query - factored out so performance_batch() can compute it
    from a pre-grouped, already-fetched row list instead."""
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
    return {"resolved": n, "accuracy": accuracy, "evidence_scope": evidence_scope,
            "recent_accuracy": recent_wins / len(recent_directional) if recent_directional else None,
            "shrunk_accuracy": posterior, "recent_shrunk_accuracy": recent_posterior,
            "realized_edge": realized_edge, "tier": tier, "directional_resolved": directional_n,
            "neutral_resolved": n - directional_n,
            "average_win_return": sum(winning_returns)/len(winning_returns) if winning_returns else None,
            "average_loss_return": sum(losing_returns)/len(losing_returns) if losing_returns else None,
            "resolved_at_latest": latest_resolved_at.isoformat() if latest_resolved_at else None}


_EMPTY_PERFORMANCE = _performance_from_rows([], "source_symbol_timeframe")


def performance_batch(db: Session, user_id: str, identities: list[tuple[str, str]], symbol: str,
                      timeframe: str, regime: str | None) -> dict[tuple[str, str], dict]:
    """Same evidence-hierarchy semantics as performance() (regime bucket,
    falling back to the all-regime bucket only when the regime bucket alone
    can't meet the minimum sample requirement), computed for every
    (source_name, source_version) identity active_drive_v2 needs to score
    in one evaluate() call, via at most 2 SQL round trips total instead of
    up to 2 per candidate (Stage 1 performance audit: ~30-35 candidates per
    request meant ~40-80 round trips here alone).

    Every source shares the same generation cadence (persist() writes
    exactly one PredictionLedger row per candidate per cycle - see
    ledger.py), so the most recent len(identities)*100 rows, grouped by
    identity, reproduce each identity's own most-recent-100 bucket exactly
    (matching performance()'s per-source .limit(100))."""
    if not identities:
        return {}
    owner_id = owner(user_id)
    fetch_limit = max(500, len(identities) * 100)

    def bucket_all(with_regime: bool):
        q = db.query(PredictionResolution, PredictionLedger.direction, PredictionLedger.source_name,
                     PredictionLedger.source_version).join(
            PredictionLedger, PredictionResolution.prediction_id == PredictionLedger.prediction_id
        ).filter(
            PredictionLedger.user_id == owner_id,
            PredictionLedger.engine == "active_drive_v2",
            PredictionLedger.symbol == symbol,
            PredictionLedger.timeframe == timeframe,
        )
        if with_regime and regime:
            q = q.filter(PredictionLedger.market_regime == regime)
        grouped: dict[tuple[str, str], list] = {}
        for resolution, direction, source_name, source_version in q.order_by(
                PredictionResolution.resolved_at.desc()).limit(fetch_limit).all():
            grouped.setdefault((source_name, source_version), []).append((resolution, direction))
        return grouped

    by_regime = bucket_all(with_regime=True)
    needs_fallback = bool(regime) and any(
        len(by_regime.get(ident, [])) < settings.active_drive_min_resolved_samples for ident in identities
    )
    by_all = bucket_all(with_regime=False) if needs_fallback else {}

    results: dict[tuple[str, str], dict] = {}
    for ident in identities:
        rows_regime = by_regime.get(ident, [])[:100]
        rows_all = by_all.get(ident, [])[:100]
        if regime and len(rows_regime) < settings.active_drive_min_resolved_samples and len(rows_all) > len(rows_regime):
            results[ident] = _performance_from_rows(rows_all, "source_symbol_timeframe")
        else:
            results[ident] = _performance_from_rows(
                rows_regime, "source_symbol_timeframe_regime" if regime else "source_symbol_timeframe")
    return results
