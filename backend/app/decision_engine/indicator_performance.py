"""Automatic indicator poor-performance / shadow / star evaluator (Bot
Settings Parts 4, 6, 10).

Single entry point: evaluate_indicator(db, source_name, source_version,
symbol, timeframe). Reads only already-resolved, trustworthy predictions
from PredictionLedger + PredictionResolution (never forming candles, never
future data - inherited for free from app.decision_engine.resolver's own
invariants: a PredictionResolution row only ever exists for a prediction
whose resolution_deadline has already passed against stored, closed
candles). Never duplicates resolution logic - this module only aggregates
what the resolver already produced.

Poor-performance rule (>= threshold wrong out of the latest window, default
7/10) is evaluated once and, when it triggers, removes the indicator's
influence from BOTH "paper" and "binance_real" modes simultaneously for
that exact (symbol, timeframe) - Part 4 describes this as one trigger with
two consequences, not two independent evaluations. The star/reactivation
check is evaluated per-mode, from that mode's own SHADOW-tagged ledger rows
(today, in practice, decisions are only ever generated with
settings_scope="paper" - see app/api/prediction.py - so binance_real shadow
history will read INSUFFICIENT_SAMPLE until a binance_real evaluation path
exists; this module makes no assumption that hides that limitation).

Idempotent: re-running with no new resolved outcomes since last_evaluated_at
is a no-op - no new history row, no new notification, no status flip within
the configured cooldown window (hysteresis, Part 10).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import (
    IndicatorEligibility,
    IndicatorEligibilityHistory,
    IndicatorPerformanceRollup,
    PredictionLedger,
    PredictionResolution,
)
from app.db.session import SessionLocal
from app.decision_engine import governance_repository, indicator_notifications
from app.decision_engine.eligibility import (
    STATUS_ACTIVE,
    STATUS_DATA_QUALITY_BLOCKED,
    STATUS_INSUFFICIENT_SAMPLE,
    STATUS_MANUALLY_DISABLED,
    STATUS_RECOMMENDED_FOR_REACTIVATION,
    STATUS_SHADOW_ONLY_POOR_PERFORMANCE,
)
from app.decision_engine.outcome import TRUSTWORTHY_STATUSES

MODES = ("paper", "binance_real")


def _classify(resolution: PredictionResolution) -> str:
    """"correct" | "wrong" | "neutral" - never silently counts a neutral
    outcome as opposite direction (Part 4)."""
    if resolution.neutral_result:
        return "neutral"
    if resolution.correct is True:
        return "correct"
    if resolution.correct is False:
        return "wrong"
    return "neutral"


def _resolved_rows(db, source_name, source_version, symbol, timeframe, execution_mode, limit=None):
    query = (
        db.query(PredictionLedger, PredictionResolution)
        .join(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(
            PredictionLedger.source_name == source_name,
            PredictionLedger.source_version == source_version,
            PredictionLedger.symbol == symbol,
            PredictionLedger.timeframe == timeframe,
            PredictionLedger.execution_mode == execution_mode,
            PredictionLedger.lifecycle_status.in_(list(TRUSTWORTHY_STATUSES)),
        )
        .order_by(PredictionLedger.generated_at.desc())
    )
    if limit:
        query = query.limit(limit)
    return query.all()


def _void_rate(db, source_name, source_version, symbol, timeframe, execution_mode, window: int) -> float:
    """Fraction of the most recent `window` ledger rows (by generated_at,
    regardless of resolution status) that ended VOID_* - a high void rate
    means the recent sample isn't trustworthy enough to evaluate numerically."""
    rows = (
        db.query(PredictionLedger)
        .filter(
            PredictionLedger.source_name == source_name, PredictionLedger.source_version == source_version,
            PredictionLedger.symbol == symbol, PredictionLedger.timeframe == timeframe,
            PredictionLedger.execution_mode == execution_mode,
            PredictionLedger.lifecycle_status.isnot(None),
        )
        .order_by(PredictionLedger.generated_at.desc())
        .limit(window)
        .all()
    )
    if not rows:
        return 0.0
    void_count = sum(1 for r in rows if r.lifecycle_status in ("VOID_DATA_GAP", "VOID_INVALID_PREDICTION"))
    return void_count / len(rows)


def _refresh_rollup(db, source_name, source_version, symbol, timeframe, execution_mode, now) -> None:
    """Upserts the persisted, safe-to-rebuild IndicatorPerformanceRollup
    cache (Part 4/7's dashboard fields) - never the source of truth, which
    remains PredictionLedger + PredictionResolution."""
    rows = _resolved_rows(db, source_name, source_version, symbol, timeframe, execution_mode)
    sample_size = len(rows)
    correct = wrong = neutral = 0
    net_returns, mfe_values, mae_values, last_10 = [], [], [], []
    for ledger, resolution in rows:
        outcome = _classify(resolution)
        if outcome == "correct":
            correct += 1
        elif outcome == "wrong":
            wrong += 1
        else:
            neutral += 1
        if len(last_10) < 10:
            last_10.append(outcome)
        if resolution.net_direction_adjusted_return is not None:
            net_returns.append(resolution.net_direction_adjusted_return)
        if resolution.maximum_favorable_excursion is not None:
            mfe_values.append(resolution.maximum_favorable_excursion)
        if resolution.maximum_adverse_excursion is not None:
            mae_values.append(resolution.maximum_adverse_excursion)

    directional = correct + wrong
    void_rate = _void_rate(db, source_name, source_version, symbol, timeframe, execution_mode, window=max(sample_size, 10))

    row = (
        db.query(IndicatorPerformanceRollup)
        .filter_by(source_name=source_name, source_version=source_version, symbol=symbol,
                   timeframe=timeframe, execution_mode=execution_mode)
        .first()
    )
    if row is None:
        row = IndicatorPerformanceRollup(source_name=source_name, source_version=source_version, symbol=symbol,
                                          timeframe=timeframe, execution_mode=execution_mode)
        db.add(row)
    row.sample_size = sample_size
    row.correct = correct
    row.wrong = wrong
    row.neutral = neutral
    row.wrong_rate = round(wrong / directional, 4) if directional else None
    row.hit_rate = round(correct / directional, 4) if directional else None
    row.net_expectancy = round(sum(net_returns) / len(net_returns), 6) if net_returns else None
    row.mfe_avg = round(sum(mfe_values) / len(mfe_values), 6) if mfe_values else None
    row.mae_avg = round(sum(mae_values) / len(mae_values), 6) if mae_values else None
    row.last_10_outcomes = last_10
    row.data_quality_flag = void_rate > 0.30
    row.computed_at = now
    db.commit()


def _get_or_create_eligibility(db, source_name, source_version, symbol, timeframe, mode) -> IndicatorEligibility:
    row = (
        db.query(IndicatorEligibility)
        .filter_by(source_name=source_name, source_version=source_version, symbol=symbol, timeframe=timeframe, mode=mode)
        .first()
    )
    if row is None:
        row = IndicatorEligibility(
            source_name=source_name, source_version=source_version, symbol=symbol, timeframe=timeframe,
            mode=mode, status=STATUS_ACTIVE, evaluation_version=0,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _in_cooldown(row: IndicatorEligibility, cooldown_hours: float, now: datetime) -> bool:
    if row.last_status_change_at is None or cooldown_hours <= 0:
        return False
    last = row.last_status_change_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last).total_seconds() < cooldown_hours * 3600


def _apply_status_change(db, row: IndicatorEligibility, *, new_status: str, status_reason: str,
                          trigger_snapshot: dict, changed_by: str, now: datetime,
                          set_shadow_since: bool = False) -> bool:
    """Idempotent: returns False (no-op) if new_status == row.status."""
    if row.status == new_status:
        row.last_evaluated_at = now
        db.commit()
        return False
    previous_status = row.status
    row.status = new_status
    row.status_reason = status_reason
    row.trigger_snapshot = trigger_snapshot
    row.last_evaluated_at = now
    row.last_status_change_at = now
    row.evaluation_version = (row.evaluation_version or 0) + 1
    if set_shadow_since:
        row.shadow_since = now
    db.add(IndicatorEligibilityHistory(
        eligibility_id=row.id, source_name=row.source_name, source_version=row.source_version,
        symbol=row.symbol, timeframe=row.timeframe, mode=row.mode, previous_status=previous_status,
        new_status=new_status, trigger_snapshot=trigger_snapshot, changed_by=changed_by, created_at=now,
    ))
    db.commit()
    db.refresh(row)
    return True


def _poor_performance_check(db, source_name, source_version, symbol, timeframe, governance, now) -> dict:
    window = governance["poor_performance_window"]
    min_sample = governance["min_sample_for_poor_performance_check"]
    rows = _resolved_rows(db, source_name, source_version, symbol, timeframe, "ACTIVE", limit=window)
    sample_size = len(rows)
    correct = wrong = neutral = 0
    for _, resolution in rows:
        outcome = _classify(resolution)
        if outcome == "correct":
            correct += 1
        elif outcome == "wrong":
            wrong += 1
        else:
            neutral += 1
    trigger_snapshot = {
        "sample_size": sample_size, "correct": correct, "wrong": wrong, "neutral": neutral,
        "wrong_rate": round(wrong / sample_size, 4) if sample_size else None,
        "symbol": symbol, "timeframe": timeframe, "evaluation_time": now.isoformat(),
        "source_version": source_version, "window": window,
    }
    if sample_size < min_sample:
        return {"status": STATUS_INSUFFICIENT_SAMPLE, "reason": f"Only {sample_size}/{min_sample} required resolved samples available",
                "trigger_snapshot": trigger_snapshot}
    if wrong >= governance["poor_performance_wrong_threshold"]:
        return {"status": STATUS_SHADOW_ONLY_POOR_PERFORMANCE,
                "reason": f"{wrong} of the latest {sample_size} resolved predictions were wrong",
                "trigger_snapshot": trigger_snapshot}
    return {"status": STATUS_ACTIVE, "reason": None, "trigger_snapshot": trigger_snapshot}


def _star_check(db, source_name, source_version, symbol, timeframe, mode, governance, shadow_since, now) -> dict:
    rows = _resolved_rows(db, source_name, source_version, symbol, timeframe, "SHADOW")
    if shadow_since is not None:
        cutoff = shadow_since if shadow_since.tzinfo else shadow_since.replace(tzinfo=timezone.utc)
        rows = [(l, r) for l, r in rows if (l.generated_at.replace(tzinfo=timezone.utc) if l.generated_at.tzinfo is None else l.generated_at) >= cutoff]
    sample_size = len(rows)
    min_required = governance["star_min_shadow_samples"]

    void_rate = _void_rate(db, source_name, source_version, symbol, timeframe, "SHADOW", window=max(sample_size, min_required))
    if void_rate > governance["data_quality_void_rate_threshold"]:
        return {"qualifies": False, "status": STATUS_DATA_QUALITY_BLOCKED,
                "detail": {"void_rate": round(void_rate, 4), "sample_size": sample_size}}

    if sample_size < min_required:
        return {"qualifies": False, "status": None, "detail": {"sample_size": sample_size, "required": min_required}}

    correct = wrong = neutral = 0
    net_returns = []
    mae_values = []
    for ledger, resolution in rows:
        outcome = _classify(resolution)
        if outcome == "correct":
            correct += 1
        elif outcome == "wrong":
            wrong += 1
        else:
            neutral += 1
        if resolution.net_direction_adjusted_return is not None:
            net_returns.append(resolution.net_direction_adjusted_return)
        if resolution.maximum_adverse_excursion is not None:
            mae_values.append(abs(resolution.maximum_adverse_excursion))

    directional = correct + wrong
    hit_rate = correct / directional if directional else 0.0
    wrong_rate = wrong / directional if directional else 0.0
    net_expectancy = sum(net_returns) / len(net_returns) if net_returns else None
    mae_avg_pct = (sum(mae_values) / len(mae_values)) * 100 if mae_values else None

    recent_n = governance["star_recent_subwindow"]
    recent = rows[:recent_n]
    recent_correct = sum(1 for _, r in recent if _classify(r) == "correct")
    recent_wrong = sum(1 for _, r in recent if _classify(r) == "wrong")
    recent_directional = recent_correct + recent_wrong
    recent_hit_rate = recent_correct / recent_directional if recent_directional else 0.0

    stats = {
        "sample_size": sample_size, "correct": correct, "wrong": wrong, "neutral": neutral,
        "hit_rate": round(hit_rate, 4), "wrong_rate": round(wrong_rate, 4),
        "net_expectancy": round(net_expectancy, 6) if net_expectancy is not None else None,
        "mae_avg_pct": round(mae_avg_pct, 4) if mae_avg_pct is not None else None,
        "recent_subwindow_hit_rate": round(recent_hit_rate, 4), "recent_subwindow_size": len(recent),
        "symbol": symbol, "timeframe": timeframe, "mode": mode,
    }

    qualifies = (
        hit_rate >= governance["star_min_hit_rate"]
        and wrong_rate <= governance["star_max_wrong_rate"]
        and net_expectancy is not None and net_expectancy > 0
        and (mae_avg_pct is None or mae_avg_pct <= governance["star_max_mae_pct"])
        # "not from one isolated winning trade / positive across a minimum
        # recent rolling window" - the recent subwindow must independently
        # clear the same hit-rate bar, not just the aggregate.
        and recent_hit_rate >= governance["star_min_hit_rate"]
    )
    return {"qualifies": qualifies, "status": STATUS_RECOMMENDED_FOR_REACTIVATION if qualifies else None, "detail": stats}


def evaluate_indicator(db, source_name: str, source_version: str, symbol: str, timeframe: str, *,
                        now: datetime | None = None, changed_by: str = "system") -> dict:
    now = now or datetime.now(timezone.utc)
    governance = governance_repository.get_settings(db=db)
    cooldown_hours = governance["status_change_cooldown_hours"]

    _refresh_rollup(db, source_name, source_version, symbol, timeframe, "ACTIVE", now)
    _refresh_rollup(db, source_name, source_version, symbol, timeframe, "SHADOW", now)

    rows_by_mode = {mode: _get_or_create_eligibility(db, source_name, source_version, symbol, timeframe, mode) for mode in MODES}

    # ---- 1. Poor-performance / active-mode check - one trigger, both modes ----
    # Only ever applies to rows currently ACTIVE or INSUFFICIENT_SAMPLE - a
    # row already SHADOW_ONLY_POOR_PERFORMANCE or RECOMMENDED_FOR_REACTIVATION
    # is exclusively managed by the star-check below from here on (no new
    # ACTIVE-execution_mode ledger rows are ever written for it again while
    # shadowed, so re-running the active-mode check would only ever see
    # stale/absent data and must never downgrade it to INSUFFICIENT_SAMPLE or
    # silently "recover" it to ACTIVE - recovery is a manual-only action).
    result = _poor_performance_check(db, source_name, source_version, symbol, timeframe, governance, now)
    for mode, row in rows_by_mode.items():
        if row.status in (STATUS_MANUALLY_DISABLED, STATUS_SHADOW_ONLY_POOR_PERFORMANCE, STATUS_RECOMMENDED_FOR_REACTIVATION):
            continue
        if _in_cooldown(row, cooldown_hours, now):
            continue
        changed = _apply_status_change(
            db, row, new_status=result["status"], status_reason=result["reason"],
            trigger_snapshot=result["trigger_snapshot"], changed_by=changed_by, now=now,
            set_shadow_since=(result["status"] == STATUS_SHADOW_ONLY_POOR_PERFORMANCE),
        )
        if changed and result["status"] == STATUS_SHADOW_ONLY_POOR_PERFORMANCE:
            indicator_notifications.notify_moved_to_shadow_only(
                source_name=source_name, symbol=symbol, timeframe=timeframe,
                evaluation_version=row.evaluation_version, trigger_snapshot=result["trigger_snapshot"], db=db,
            )

    # ---- 2. Star / reactivation-recommended check - per mode, shadow-only rows ----
    for mode, row in rows_by_mode.items():
        db.refresh(row)
        if row.status not in (STATUS_SHADOW_ONLY_POOR_PERFORMANCE, STATUS_RECOMMENDED_FOR_REACTIVATION):
            continue
        if _in_cooldown(row, cooldown_hours, now) and row.status == STATUS_RECOMMENDED_FOR_REACTIVATION:
            continue
        star = _star_check(db, source_name, source_version, symbol, timeframe, mode, governance, row.shadow_since, now)
        if star["status"] == STATUS_DATA_QUALITY_BLOCKED:
            changed = _apply_status_change(db, row, new_status=STATUS_DATA_QUALITY_BLOCKED,
                                            status_reason="Recent void/data-gap rate too high to evaluate reliably",
                                            trigger_snapshot=star["detail"], changed_by=changed_by, now=now)
            if changed:
                indicator_notifications.notify_insufficient_data_quality(
                    source_name=source_name, symbol=symbol, timeframe=timeframe,
                    evaluation_version=row.evaluation_version, detail=star["detail"], db=db)
            continue
        if star["qualifies"] and row.status == STATUS_SHADOW_ONLY_POOR_PERFORMANCE:
            changed = _apply_status_change(db, row, new_status=STATUS_RECOMMENDED_FOR_REACTIVATION,
                                            status_reason="Shadow performance meets reactivation criteria",
                                            trigger_snapshot=star["detail"], changed_by=changed_by, now=now)
            if changed:
                indicator_notifications.notify_recommended_for_reactivation(
                    source_name=source_name, symbol=symbol, timeframe=timeframe,
                    evaluation_version=row.evaluation_version, stats=star["detail"], db=db)
        elif not star["qualifies"] and row.status == STATUS_RECOMMENDED_FOR_REACTIVATION:
            # Deterioration: the star can be automatically withdrawn (never
            # auto-reactivated) - revoking a recommendation isn't activating
            # anything.
            changed = _apply_status_change(db, row, new_status=STATUS_SHADOW_ONLY_POOR_PERFORMANCE,
                                            status_reason="Shadow performance no longer meets reactivation criteria",
                                            trigger_snapshot=star["detail"], changed_by=changed_by, now=now)
            if changed:
                indicator_notifications.notify_shadow_performance_deteriorated(
                    source_name=source_name, symbol=symbol, timeframe=timeframe,
                    evaluation_version=row.evaluation_version, stats=star["detail"], db=db)

    return {mode: {"status": row.status, "evaluation_version": row.evaluation_version} for mode, row in rows_by_mode.items()}


def evaluate_recent_indicators(db=None, *, lookback_hours: int = 24, batch_size: int = 200) -> int:
    """Periodic tick (see app.decision_engine.scheduler): evaluates every
    distinct (source_name, source_version, symbol, timeframe) tuple seen in
    PredictionLedger within the last `lookback_hours`, bounded to
    `batch_size` tuples per call - same "don't hammer" philosophy as the
    resolver's backoff."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        since = datetime.now(timezone.utc).timestamp() - lookback_hours * 3600
        since_dt = datetime.fromtimestamp(since, tz=timezone.utc)
        tuples = (
            db.query(PredictionLedger.source_name, PredictionLedger.source_version,
                      PredictionLedger.symbol, PredictionLedger.timeframe)
            .filter(PredictionLedger.generated_at >= since_dt)
            .distinct()
            .limit(batch_size)
            .all()
        )
        for source_name, source_version, symbol, timeframe in tuples:
            evaluate_indicator(db, source_name, source_version, symbol, timeframe)
        return len(tuples)
    finally:
        if owns_session:
            db.close()
