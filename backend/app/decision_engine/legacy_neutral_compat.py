"""Narrow, idempotent, versioned legacy-field compatibility backfill
(2026-07-21 resolver audit follow-up).

This fixes a legacy DISPLAY-field staleness bug, not a lifecycle-status bug.
The 2026-07-21 full-population audit (130,528 BTC/ETH fixed_horizon_close
rows with PredictionResolution.correct IS NULL) proved lifecycle_status =
RESOLVED_NEUTRAL is already correct for every one of those rows: each is
either a NO_TRADE/NEUTRAL prediction (no directional claim to score) or a
LONG/SHORT prediction whose realised move never cleared the neutral band.
Only the legacy PredictionResolution.neutral_result boolean was never
populated to match, because it predates the lifecycle_status column.

Scope note (deliberately narrow): this predicate only touches the subset of
that audited population where resolved_direction is itself NEUTRAL (~38,144
rows) - LONG/SHORT predictions that landed in the neutral band. It does NOT
touch NO_TRADE/NEUTRAL predictions whose resolved_direction was LONG or SHORT
(~92,384 rows) - those are equally lifecycle_status=RESOLVED_NEUTRAL, but the
requested predicate requires resolved_direction=NEUTRAL literally, so those
rows are intentionally left out of THIS backfill. Old panels reading
neutral_result directly will therefore still show a lower neutral count than
the new lifecycle-status-based panels for that remaining subset until a
separately-authorized, broader predicate is run.

This module NEVER changes lifecycle_status, resolved_direction, reference
price, outcome price, prediction direction, resolution timestamps, or
provenance. It never sets correct to True or False. It only ever sets
PredictionResolution.neutral_result = True for rows matching the exact
predicate below, and records one audit row per corrected prediction_id.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import LegacyNeutralCompatCorrection, PredictionLedger, PredictionResolution
from app.decision_engine import outcome as outcome_mod
from app.monitoring.logging import get_logger, log_event

logger = get_logger("quantx.legacy_neutral_compat")

CORRECTION_VERSION = "legacy_neutral_compatibility_backfill_v1"
AUDITED_SYMBOLS = ("BTCUSDT", "ETHUSDT")
AUDITED_RESOLUTION_REASON = "fixed_horizon_close"


def _predicate_query(db: Session):
    """Every clause mirrors the completed 2026-07-21 audit verbatim. Do not
    broaden this without re-running that audit against the broadened set
    first and getting separate authorization - see module docstring."""
    already_corrected = db.query(LegacyNeutralCompatCorrection.prediction_id)
    return (
        db.query(PredictionLedger, PredictionResolution)
        .join(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(
            PredictionLedger.symbol.in_(AUDITED_SYMBOLS),
            PredictionLedger.lifecycle_status == outcome_mod.RESOLVED_NEUTRAL,
            PredictionResolution.resolved_direction == "NEUTRAL",
            PredictionResolution.correct.is_(None),
            or_(PredictionResolution.neutral_result.is_(None), PredictionResolution.neutral_result.is_(False)),
            PredictionResolution.resolution_reason == AUDITED_RESOLUTION_REASON,
            PredictionLedger.reference_price.isnot(None),
            PredictionResolution.actual_return.isnot(None),
            PredictionLedger.direction.isnot(None),
            ~PredictionLedger.prediction_id.in_(already_corrected),
        )
    )


def dry_run(db: Session) -> dict:
    """Reports exactly what apply() would change, without writing anything."""
    matched = _predicate_query(db).all()
    by_prediction_direction: dict[str, int] = {}
    already_true = (
        db.query(PredictionResolution)
        .join(PredictionLedger, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(
            PredictionLedger.symbol.in_(AUDITED_SYMBOLS),
            PredictionLedger.lifecycle_status == outcome_mod.RESOLVED_NEUTRAL,
            PredictionResolution.resolved_direction == "NEUTRAL",
            PredictionResolution.correct.is_(None),
            PredictionResolution.neutral_result.is_(True),
            PredictionResolution.resolution_reason == AUDITED_RESOLUTION_REASON,
        )
        .count()
    )
    for ledger, _ in matched:
        by_prediction_direction[ledger.direction] = by_prediction_direction.get(ledger.direction, 0) + 1
    # Rejected-by-predicate: same resolution_reason/correct-NULL population,
    # but resolved_direction != NEUTRAL (out of scope for this narrow predicate).
    rejected_not_neutral_resolved_direction = (
        db.query(PredictionResolution)
        .join(PredictionLedger, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(
            PredictionLedger.symbol.in_(AUDITED_SYMBOLS),
            PredictionResolution.resolution_reason == AUDITED_RESOLUTION_REASON,
            PredictionResolution.correct.is_(None),
            PredictionResolution.resolved_direction != "NEUTRAL",
        )
        .count()
    )
    return {
        "matched_row_count": len(matched),
        "by_prediction_direction": by_prediction_direction,
        "long_count": by_prediction_direction.get("LONG", 0),
        "short_count": by_prediction_direction.get("SHORT", 0),
        "no_trade_count": by_prediction_direction.get("NO_TRADE", 0),
        "neutral_direction_count": by_prediction_direction.get("NEUTRAL", 0),
        "already_neutral_result_true": already_true,
        "rejected_resolved_direction_not_neutral": rejected_not_neutral_resolved_direction,
        "correction_version": CORRECTION_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def apply(db: Session, batch_size: int = 2000) -> dict:
    """Applies the backfill in bounded batches, one committed transaction per
    batch, recording one audit row per corrected prediction_id. Safe to
    interrupt and resume: each committed batch is final (the predicate
    excludes already-corrected rows via the audit table), so a re-run after
    interruption simply continues from wherever it stopped. A second full
    run after completion matches zero additional rows (see also: the
    predicate itself excludes rows already carrying neutral_result=True)."""
    total_corrected = 0
    while True:
        batch = _predicate_query(db).limit(batch_size).all()
        if not batch:
            break
        now = datetime.now(timezone.utc)
        for ledger, resolution in batch:
            old_value = resolution.neutral_result
            resolution.neutral_result = True
            db.add(LegacyNeutralCompatCorrection(
                prediction_id=ledger.prediction_id,
                correction_version=CORRECTION_VERSION,
                correction_timestamp=now,
                old_neutral_result=old_value,
                new_neutral_result=True,
                audit_reason=CORRECTION_VERSION,
            ))
        db.commit()
        total_corrected += len(batch)
        log_event(logger, message="legacy_neutral_compat_batch_applied", category="prediction",
                  batch_size=len(batch), total_corrected=total_corrected)
    return {"corrected_row_count": total_corrected, "correction_version": CORRECTION_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat()}
