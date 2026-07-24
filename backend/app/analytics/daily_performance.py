"""Daily performance snapshot aggregation (main-purpose consolidation, Stage 2).

Aggregates PredictionLedger (+ PredictionResolution) - the same row-level
source of truth app.decision_engine.repository.performance()/
performance_batch() already use for live evidence-gating - into immutable,
one-row-per-day-per-scope snapshots: DailyIndicatorPerformance,
DailyStrategyPerformance, DailyModelPerformance, DailyQuantSignalPerformance,
DailyV2Performance (app.db.models).

Idempotent and restart-safe: every write is an upsert keyed by the table's
UniqueConstraint (date, symbol, timeframe, contributor identity, direction,
market_regime, engine_version), never an append - re-running the same UTC
date recomputes and overwrites that date's rows rather than duplicating
them. Recalculable and auditable: every row carries generated_at,
generation_version, and a deterministic fingerprint of its own inputs.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date as date_type, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    ActiveDriveDecision,
    DailyIndicatorPerformance,
    DailyModelPerformance,
    DailyQuantSignalPerformance,
    DailyStrategyPerformance,
    DailyV2Performance,
    PredictionLedger,
    PredictionResolution,
)
from app.db.session import SessionLocal
from app.decision_engine.outcome import RESOLVED_STATUSES, VOID_STATUSES
from app.monitoring.logging import get_logger

logger = get_logger("quantx.analytics.daily_performance")

GENERATION_VERSION = "1.0.0"

_UNRESOLVED_NOT_DUE_STATUSES = frozenset({"PENDING", "RESOLVING", "RESOLUTION_ERROR_RETRYING"})

_TYPE_TABLES = {
    "strategy": DailyStrategyPerformance,
    "ml": DailyModelPerformance,
    "quant": DailyQuantSignalPerformance,
}


def _signed_return(actual_return: float | None, direction: str | None) -> float | None:
    """Same convention as app.decision_engine.repository._signed_return:
    positive when the outcome moved in the predicted direction, negative
    otherwise - so win/loss contribution and average error are directional,
    not raw price-change magnitude."""
    if actual_return is None or direction not in ("LONG", "SHORT"):
        return None
    return actual_return if direction == "LONG" else -actual_return


def _wilson_interval(correct: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for a binomial proportion - stable for
    small samples (unlike the naive normal approximation), gated by the
    same minimum-sample-size bar the engine already uses for evidence
    (settings.active_drive_min_resolved_samples) so a 2/2 bucket never
    reports a misleadingly tight interval."""
    if total < settings.active_drive_min_resolved_samples:
        return None
    p = correct / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = (z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _fingerprint(scope: dict, metrics: dict) -> str:
    payload = {**scope, **metrics}
    safe = {k: v for k, v in payload.items() if isinstance(v, (str, int, float, bool, type(None)))}
    return hashlib.sha256(json.dumps(safe, sort_keys=True, default=str).encode()).hexdigest()


def _bucket_metrics(rows: list[tuple]) -> dict:
    """rows: list of (PredictionLedger, PredictionResolution|None) tuples,
    all already filtered to one (date, symbol, timeframe, contributor,
    direction, market_regime, engine_version) scope."""
    total = len(rows)
    resolved = [(l, r) for l, r in rows if l.lifecycle_status in RESOLVED_STATUSES and r is not None]
    void = [l for l, _ in rows if l.lifecycle_status in VOID_STATUSES]
    not_due = [l for l, _ in rows if l.lifecycle_status in _UNRESOLVED_NOT_DUE_STATUSES]

    correct = sum(1 for l, _ in resolved if l.lifecycle_status == "RESOLVED_CORRECT")
    wrong = sum(1 for l, _ in resolved if l.lifecycle_status == "RESOLVED_WRONG")
    neutral = sum(1 for l, _ in resolved if l.lifecycle_status == "RESOLVED_NEUTRAL")
    directional_n = correct + wrong

    signed_returns = [
        sr for l, r in resolved
        if (sr := _signed_return(r.actual_return, l.direction)) is not None
    ]
    win_returns = [
        sr for l, r in resolved
        if l.lifecycle_status == "RESOLVED_CORRECT" and (sr := _signed_return(r.actual_return, l.direction)) is not None
    ]
    loss_returns = [
        sr for l, r in resolved
        if l.lifecycle_status == "RESOLVED_WRONG" and (sr := _signed_return(r.actual_return, l.direction)) is not None
    ]
    errors = [abs(sr) for sr in signed_returns]

    generated_ats = [l.generated_at for l, _ in rows if l.generated_at is not None]
    ci = _wilson_interval(correct, directional_n) if directional_n else None

    return {
        "total_eligible_predictions": total,
        "resolved_predictions": len(resolved),
        "correct": correct,
        "wrong": wrong,
        "neutral": neutral,
        "unresolved_not_due": len(not_due),
        "coverage_rate": round(len(resolved) / total, 4) if total else None,
        "directional_accuracy": round(correct / directional_n, 4) if directional_n else None,
        "neutral_rate": round(neutral / len(resolved), 4) if resolved else None,
        "average_error": round(sum(errors) / len(errors), 6) if errors else None,
        "median_error": round(_median(errors), 6) if errors else None,
        "average_actual_return": round(sum(signed_returns) / len(signed_returns), 6) if signed_returns else None,
        "win_contribution": round(sum(win_returns), 6) if win_returns else None,
        "loss_contribution": round(sum(loss_returns), 6) if loss_returns else None,
        "sample_size": directional_n,
        "confidence_interval_low": round(ci[0], 4) if ci else None,
        "confidence_interval_high": round(ci[1], 4) if ci else None,
        "first_prediction_at": min(generated_ats) if generated_ats else None,
        "last_prediction_at": max(generated_ats) if generated_ats else None,
        "data_quality_status": "partial_data_gaps" if void else "ok",
    }


def _upsert(db: Session, model, scope: dict, metrics: dict) -> None:
    filters = [getattr(model, k) == v for k, v in scope.items()]
    existing = db.query(model).filter(*filters).first()
    fingerprint = _fingerprint(scope, metrics)
    values = {
        **scope, **metrics,
        "generated_at": datetime.now(timezone.utc),
        "generation_version": GENERATION_VERSION,
        "fingerprint": fingerprint,
    }
    if existing is not None:
        for k, v in values.items():
            setattr(existing, k, v)
    else:
        db.add(model(**values))


def run_daily_aggregation(target_date: date_type, db: Session | None = None) -> dict:
    """Compute and upsert every daily_* snapshot for one UTC calendar date.
    Safe to call more than once for the same date (idempotent upsert, never
    an append) and safe to call for a date that already has more
    predictions resolve after this ran (simply recomputes and overwrites)."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        ledger_rows = (
            db.query(PredictionLedger)
            .filter(PredictionLedger.generated_at >= day_start, PredictionLedger.generated_at < day_end)
            .all()
        )
        if not ledger_rows:
            return {"date": target_date.isoformat(), "scopes_written": 0, "rows_considered": 0}

        prediction_ids = [l.prediction_id for l in ledger_rows]
        resolutions = {
            r.prediction_id: r
            for r in db.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(prediction_ids)).all()
        }

        buckets: dict[tuple, list[tuple]] = {}
        for l in ledger_rows:
            key = (l.symbol, l.timeframe, l.source_type, l.source_name, l.source_version,
                   l.direction, l.market_regime, l.engine_version)
            buckets.setdefault(key, []).append((l, resolutions.get(l.prediction_id)))

        # Umbrella "indicator" buckets ignore source_type in the grouping key
        # (mirrors IndicatorPerformanceRollup's existing cross-type
        # source_name/source_version key) - a contributor keeps the same
        # name/version across types in practice, so this does not merge
        # unrelated contributors.
        indicator_buckets: dict[tuple, list[tuple]] = {}
        for l in ledger_rows:
            key = (l.symbol, l.timeframe, l.source_type, l.source_name, l.source_version,
                   l.direction, l.market_regime, l.engine_version)
            ind_key = (l.symbol, l.timeframe, l.source_name, l.source_version,
                       l.direction, l.market_regime, l.engine_version)
            indicator_buckets.setdefault(ind_key, []).append((l, resolutions.get(l.prediction_id)))

        # V2-level buckets: the decision's OWN final signal vs. the actual
        # market outcome, not any one contributor's individual vote -
        # one representative resolution per decision_id (all candidates
        # under one decision share the same underlying market timing).
        decision_ids = {l.decision_id for l in ledger_rows}
        decisions = {
            d.decision_id: d
            for d in db.query(ActiveDriveDecision).filter(ActiveDriveDecision.decision_id.in_(decision_ids)).all()
        }
        v2_seen_decisions: set[str] = set()
        v2_buckets: dict[tuple, list[tuple]] = {}
        for l in ledger_rows:
            decision = decisions.get(l.decision_id)
            if decision is None or l.decision_id in v2_seen_decisions:
                continue
            v2_seen_decisions.add(l.decision_id)
            key = (decision.symbol, decision.timeframe, decision.signal, l.market_regime, decision.engine_version)
            v2_buckets.setdefault(key, []).append((l, resolutions.get(l.prediction_id)))

        scopes_written = 0

        for (symbol, timeframe, source_type, name, version, direction, regime, engine_version), rows in buckets.items():
            table = _TYPE_TABLES.get(source_type)
            if table is None:
                continue
            scope = {
                "date": target_date, "symbol": symbol, "timeframe": timeframe,
                "contributor_name": name, "contributor_version": version,
                "direction": direction, "market_regime": regime, "engine_version": engine_version,
            }
            _upsert(db, table, scope, _bucket_metrics(rows))
            scopes_written += 1

        for (symbol, timeframe, name, version, direction, regime, engine_version), rows in indicator_buckets.items():
            # contributor_type on the umbrella row is informational (the
            # type of whichever rows landed in this name/version bucket -
            # names are effectively unique per type in practice).
            contributor_type = rows[0][0].source_type
            scope = {
                "date": target_date, "symbol": symbol, "timeframe": timeframe,
                "contributor_type": contributor_type, "contributor_name": name, "contributor_version": version,
                "direction": direction, "market_regime": regime, "engine_version": engine_version,
            }
            _upsert(db, DailyIndicatorPerformance, scope, _bucket_metrics(rows))
            scopes_written += 1

        for (symbol, timeframe, signal, regime, engine_version), rows in v2_buckets.items():
            scope = {
                "date": target_date, "symbol": symbol, "timeframe": timeframe,
                "direction": signal, "market_regime": regime, "engine_version": engine_version,
            }
            metrics = _bucket_metrics(rows)
            decisions_in_scope = [decisions[l.decision_id] for l, _ in rows if l.decision_id in decisions]
            metrics["execution_approved_count"] = sum(1 for d in decisions_in_scope if d.execution_approved)
            metrics["execution_blocked_count"] = sum(1 for d in decisions_in_scope if not d.execution_approved)
            _upsert(db, DailyV2Performance, scope, metrics)
            scopes_written += 1

        db.commit()
        return {
            "date": target_date.isoformat(),
            "scopes_written": scopes_written,
            "rows_considered": len(ledger_rows),
        }
    finally:
        if owns_session:
            db.close()
