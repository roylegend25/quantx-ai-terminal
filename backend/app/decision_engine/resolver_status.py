"""Read-only aggregation for the resolver/accuracy dashboard surfaces.

Every query here is SQL-side aggregation (func.count/func.sum/GROUP BY) - no
endpoint in this module ever hydrates the full prediction_ledger table into
Python. At 150k+ rows and growing, that distinction is the difference
between a sub-100ms dashboard call and a multi-second one holding a DB
connection while it walks every row.
"""
from datetime import datetime, timezone

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.data_sources import symbol_map
from app.db.models import PredictionLedger, PredictionResolution
from app.decision_engine import outcome
from app.decision_engine import scheduler as resolver_scheduler
from app.decision_engine.resolver import UNRESOLVED_STATUSES

MIN_SAMPLE_FOR_ACCURACY = 20


def unresolved_reason_summary(db: Session, symbol: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    q = (
        db.query(PredictionLedger.symbol, PredictionLedger.unresolved_status, func.count(PredictionLedger.prediction_id))
        .outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(PredictionResolution.id.is_(None))
    )
    if symbol:
        q = q.filter(PredictionLedger.symbol == symbol.upper())
    q = q.group_by(PredictionLedger.symbol, PredictionLedger.unresolved_status)

    out: dict[str, dict[str, int]] = {}
    for sym, status, count in q.all():
        status = status or "due_for_resolution"  # never-attempted rows have NULL status until the first cycle touches them
        out.setdefault(sym, {}).setdefault(status, 0)
        out[sym][status] += count

    # Rows never touched by a resolver cycle (unresolved_status still NULL)
    # need the due/not-due split computed live, since that status is set lazily.
    not_due_q = (
        db.query(PredictionLedger.symbol, func.count(PredictionLedger.prediction_id))
        .outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(PredictionResolution.id.is_(None), PredictionLedger.unresolved_status.is_(None),
                PredictionLedger.resolution_deadline > now)
    )
    if symbol:
        not_due_q = not_due_q.filter(PredictionLedger.symbol == symbol.upper())
    for sym, count in not_due_q.group_by(PredictionLedger.symbol).all():
        out.setdefault(sym, {})
        due_count = out[sym].pop("due_for_resolution", 0)
        out[sym]["awaiting_horizon"] = out[sym].get("awaiting_horizon", 0) + count
        remaining_due = due_count - count
        if remaining_due > 0:
            out[sym]["due_for_resolution"] = remaining_due

    return {"symbols": out, "statuses": list(UNRESOLVED_STATUSES), "generated_at": now.isoformat()}


def catchup_progress(db: Session) -> dict:
    """total_due/total_overdue (and the "healthy" flag derived from them in
    the /resolver/health route) must count only predictions still genuinely
    actionable by the resolver - a row already terminally classified
    VOID_DATA_GAP/VOID_INVALID_PREDICTION (lifecycle_status) or
    permanent_data_gap (the legacy unresolved_status marker) has already
    reached a terminal state; the resolver will never attempt it again
    (see resolver.py's UNRESOLVED_STATUSES/_claim_rows_fair, which both
    exclude permanent_data_gap from claiming). Counting those rows as
    "overdue" here previously reported a large, alarming backlog
    (thousands of rows) that was actually a correct, honest historical
    market-data gap, not a stuck resolver - lifecycle_health() below
    already excluded them correctly; this brings catchup_progress()/
    resolver_health() in line with it."""
    now = datetime.now(timezone.utc)
    not_terminally_voided = or_(
        PredictionLedger.lifecycle_status.is_(None),
        PredictionLedger.lifecycle_status.notin_(outcome.VOID_STATUSES),
    )
    # All unresolved rows regardless of terminal status - delayed/
    # permanently_failed below deliberately need to see the
    # permanent_data_gap rows themselves, not have them excluded.
    all_unresolved = db.query(PredictionLedger).outerjoin(
        PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id
    ).filter(PredictionResolution.id.is_(None))
    # Still genuinely actionable: excludes rows already terminally voided
    # (VOID_DATA_GAP/VOID_INVALID_PREDICTION/permanent_data_gap) - this is
    # what total_due/total_overdue/per-symbol/oldest_overdue below, and the
    # "healthy" flag derived from them, should count.
    base = all_unresolved.filter(
        not_terminally_voided,
        or_(PredictionLedger.unresolved_status.is_(None), PredictionLedger.unresolved_status != "permanent_data_gap"),
    )

    # The anti-join above (LEFT JOIN + IS NULL) forces a full scan of
    # prediction_ledger - unavoidable to compute "still unresolved" without a
    # dedicated indexed column. What *is* avoidable is re-running that same
    # scan once per .count()/.first() call: the code below used to issue 9
    # separate executions of it (total_due, total_overdue, delayed,
    # permanently_failed, 2x per symbol x2, oldest) which measured at 30+
    # seconds against the live ~280k-row ledger - well past the deploy
    # health-check timeout. Collapsing each independent shape into a single
    # grouped conditional-aggregation query does the same classification in
    # one scan instead of many.
    due_case = case((PredictionLedger.resolution_deadline <= now, 1), else_=0)
    overdue_case = case(
        (and_(PredictionLedger.resolution_deadline <= now, PredictionLedger.resolver_attempts > 0), 1), else_=0
    )
    oldest_due_case = case((PredictionLedger.resolution_deadline <= now, PredictionLedger.resolution_deadline))
    due_overdue_rows = (
        base.with_entities(
            PredictionLedger.symbol,
            func.sum(due_case),
            func.sum(overdue_case),
            func.min(oldest_due_case),
        )
        .group_by(PredictionLedger.symbol)
        .all()
    )
    total_due = sum(row[1] or 0 for row in due_overdue_rows)
    total_overdue = sum(row[2] or 0 for row in due_overdue_rows)
    oldest_deadlines = [row[3] for row in due_overdue_rows if row[3] is not None]
    oldest_deadline = min(oldest_deadlines) if oldest_deadlines else None
    per_symbol = {sym: {"due": 0, "overdue": 0} for sym in ("BTCUSDT", "ETHUSDT")}
    for sym, due, overdue, _ in due_overdue_rows:
        if sym in per_symbol:
            per_symbol[sym] = {"due": due or 0, "overdue": overdue or 0}

    processed = db.query(func.count(PredictionLedger.prediction_id)).filter(
        PredictionLedger.symbol.in_(("BTCUSDT", "ETHUSDT")), PredictionLedger.resolver_attempts > 0
    ).scalar() or 0
    resolved_total = db.query(func.count(PredictionResolution.id)).join(
        PredictionLedger, PredictionResolution.prediction_id == PredictionLedger.prediction_id
    ).filter(PredictionLedger.symbol.in_(("BTCUSDT", "ETHUSDT"))).scalar() or 0

    delayed_case = case(
        (PredictionLedger.unresolved_status.in_(
            ("resolver_delayed", "secondary_provider_pending", "primary_provider_unavailable", "primary_market_data_gap")
        ), 1), else_=0
    )
    permanent_case = case((PredictionLedger.unresolved_status == "permanent_data_gap", 1), else_=0)
    delayed, permanently_failed = (
        all_unresolved.filter(PredictionLedger.symbol.in_(("BTCUSDT", "ETHUSDT")))
        .with_entities(func.sum(delayed_case), func.sum(permanent_case))
        .first()
    )
    delayed = delayed or 0
    permanently_failed = permanently_failed or 0

    oldest_age_seconds = (now - oldest_deadline.replace(tzinfo=timezone.utc)).total_seconds() if oldest_deadline else None

    status = resolver_scheduler.status()
    last_stats = status.get("last_stats") or {}
    return {
        "total_due": total_due,
        "total_overdue": total_overdue,
        "btc": per_symbol["BTCUSDT"],
        "eth": per_symbol["ETHUSDT"],
        "resolved_this_run": last_stats.get("resolved", 0),
        "failed_this_run": last_stats.get("failed", 0),
        "remaining": total_due,
        "processed_total": processed,
        "resolved_total": resolved_total,
        "delayed_total": delayed,
        "permanently_failed_total": permanently_failed,
        "primary_source_resolutions": last_stats.get("primary_source", 0),
        "fallback_resolutions": last_stats.get("fallback_source", 0),
        "provider_disagreement_count": last_stats.get("provider_disagreement", 0),
        "oldest_overdue_age_seconds": oldest_age_seconds,
        "last_run": status.get("last_run"),
        "last_success": status.get("last_success"),
        "last_error": status.get("last_error"),
        "estimated_completion": None if total_due == 0 or not last_stats.get("resolved") else
            f"~{round(total_due / max(last_stats['resolved'], 1))} more cycles at the current per-cycle resolution rate",
    }


def lifecycle_health(db: Session) -> dict:
    """Explicit lifecycle-status view (app.decision_engine.outcome) - additive
    to unresolved_reason_summary/catchup_progress above, which remain the
    unresolved_status-based views. Distinguishes PENDING (horizon not yet
    closed - never a resolver problem) from every other state, and reports
    both queues' worker health independently so the two-queue split is
    directly observable rather than inferred.

    Every query here explicitly checks for an existing PredictionResolution
    row before treating a NULL/PENDING-stored lifecycle_status as still
    outstanding: a resolved row's lifecycle_status can be NULL only because
    it was resolved before this column existed (see the one-time backfill,
    app.decision_engine.resolver_backfill) - it must never be miscounted as
    still pending just because the column itself is unbackfilled."""
    now = datetime.now(timezone.utc)
    btc_eth = PredictionLedger.symbol.in_(("BTCUSDT", "ETHUSDT"))
    has_resolution = PredictionLedger.prediction_id.in_(db.query(PredictionResolution.prediction_id))

    counts = dict(
        db.query(PredictionLedger.lifecycle_status, func.count(PredictionLedger.prediction_id))
        .filter(btc_eth).group_by(PredictionLedger.lifecycle_status).all()
    )
    # A resolved row with a NULL/PENDING stored lifecycle_status (pre-backfill
    # legacy) is reclassified from its actual resolution outcome here, for
    # display only - this never writes to the DB.
    legacy_resolved_breakdown = dict(
        db.query(
            case((PredictionResolution.correct.is_(True), outcome.RESOLVED_CORRECT),
                 (PredictionResolution.correct.is_(False), outcome.RESOLVED_WRONG),
                 else_=outcome.RESOLVED_NEUTRAL),
            func.count(PredictionLedger.prediction_id),
        )
        .join(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(btc_eth, or_(PredictionLedger.lifecycle_status.is_(None), PredictionLedger.lifecycle_status == outcome.PENDING))
        .group_by(PredictionResolution.correct)
        .all()
    )
    # Rows still stored as PENDING (or NULL, pre-migration legacy rows) whose
    # deadline has actually passed AND that have no resolution yet are
    # live-derived as RESOLVING for display, exactly as
    # outcome.effective_lifecycle_status does per-row - done here as one
    # aggregate query instead of a python loop over 200k+ rows.
    stored_pending_matured = (
        db.query(func.count(PredictionLedger.prediction_id))
        .filter(btc_eth, PredictionLedger.resolution_deadline <= now, ~has_resolution,
                or_(PredictionLedger.lifecycle_status.is_(None), PredictionLedger.lifecycle_status == outcome.PENDING))
        .scalar() or 0
    )
    stored_pending_not_matured = (
        db.query(func.count(PredictionLedger.prediction_id))
        .filter(btc_eth, PredictionLedger.resolution_deadline > now, ~has_resolution,
                or_(PredictionLedger.lifecycle_status.is_(None), PredictionLedger.lifecycle_status == outcome.PENDING))
        .scalar() or 0
    )
    display_counts = {k: v for k, v in counts.items() if k not in (None, outcome.PENDING)}
    display_counts[outcome.PENDING] = stored_pending_not_matured
    display_counts[outcome.RESOLVING] = display_counts.get(outcome.RESOLVING, 0) + stored_pending_matured
    for status, count in legacy_resolved_breakdown.items():
        display_counts[status] = display_counts.get(status, 0) + count

    oldest_pending = (
        db.query(PredictionLedger.resolution_deadline)
        .filter(btc_eth, PredictionLedger.resolution_deadline <= now, ~has_resolution,
                or_(PredictionLedger.lifecycle_status.is_(None), PredictionLedger.lifecycle_status.in_(
                    (outcome.PENDING, outcome.RESOLVING, outcome.RESOLUTION_ERROR_RETRYING))))
        .order_by(PredictionLedger.resolution_deadline).first()
    )
    oldest_pending_age_seconds = (now - oldest_pending[0].replace(tzinfo=timezone.utc)).total_seconds() if oldest_pending and oldest_pending[0] else None

    latest_resolved = db.query(func.max(PredictionResolution.resolved_at)).join(
        PredictionLedger, PredictionResolution.prediction_id == PredictionLedger.prediction_id
    ).filter(btc_eth).scalar()

    queues = resolver_scheduler.queue_status()
    return {
        "counts": display_counts,
        "oldest_matured_pending_age_seconds": oldest_pending_age_seconds,
        "latest_resolved_prediction_at": latest_resolved.isoformat() if latest_resolved else None,
        "queues": queues,
        "generated_at": now.isoformat(),
    }


def provider_health() -> dict:
    """Static capability/config report - not a live ping (avoids spending
    rate-limit budget just to render a health badge)."""
    return {
        "providers": [
            {"provider": "binance_futures", "role": "primary", "market_type": "usdt_perp", "enabled": True},
            {"provider": "coinbase", "role": "fallback", "market_type": "spot", "enabled": True},
            {"provider": "bybit", "role": "fallback", "market_type": "usdt_perp", "enabled": True},
            {"provider": "okx", "role": "fallback", "market_type": "usdt_swap", "enabled": True},
            {"provider": "hyperliquid", "role": "fallback", "market_type": "usdt_perp", "enabled": True},
            {"provider": "binance_spot", "role": "fallback", "market_type": "spot", "enabled": False},
        ],
        "canonical_symbols": list(symbol_map.CANONICAL_SYMBOLS.keys()),
    }


def _accuracy_row(db: Session, group_col, filters: list, combined_key: str | None = None) -> list[dict]:
    """group_col=None aggregates everything into one row (the "combined"
    summary, labeled `combined_key` in Python) instead of GROUP BY - simpler
    and more portable across SQLite/Postgres than a SQL literal() pseudo-column."""
    columns = [
        func.count(PredictionLedger.prediction_id).label("total"),
        func.sum(case((PredictionResolution.id.isnot(None), 1), else_=0)).label("resolved"),
        func.sum(case((PredictionResolution.correct.is_(True), 1), else_=0)).label("correct"),
        func.sum(case((PredictionResolution.correct.is_(False), 1), else_=0)).label("wrong"),
        func.sum(case((PredictionResolution.neutral_result.is_(True), 1), else_=0)).label("neutral"),
        func.sum(case((PredictionLedger.resolution_deadline > datetime.now(timezone.utc), 1), else_=0)).label("not_due"),
        func.avg(PredictionResolution.actual_return).label("avg_return"),
        func.min(PredictionLedger.generated_at).label("first_prediction"),
        func.max(PredictionLedger.generated_at).label("latest_prediction"),
        func.max(PredictionResolution.resolved_at).label("latest_resolution"),
    ]
    if group_col is not None:
        columns.insert(0, group_col.label("key"))
    q = db.query(*columns).outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
    for f in filters:
        q = q.filter(f)
    if group_col is not None:
        q = q.group_by(group_col)

    out = []
    for row in q.all():
        key = row[0] if group_col is not None else (combined_key or "combined")
        total, resolved, correct, wrong, neutral, not_due, avg_return, first_p, latest_p, latest_r = \
            row[1:] if group_col is not None else row
        resolved = resolved or 0
        correct = correct or 0
        wrong = wrong or 0
        directional = correct + wrong
        eligible = resolved - (neutral or 0)  # resolved-eligible excludes neutral from the directional-accuracy denominator
        overdue = max(0, total - resolved - (not_due or 0))
        out.append({
            "key": str(key) if key is not None else "unknown/legacy",
            "total_predictions": total,
            "resolved_eligible": resolved,
            "correct": correct,
            "wrong": wrong,
            "neutral": neutral or 0,
            "unresolved_not_due": not_due or 0,
            "overdue_unresolved": overdue,
            "directional_accuracy": round(correct / directional, 4) if directional >= MIN_SAMPLE_FOR_ACCURACY else None,
            "neutral_rate": round((neutral or 0) / resolved, 4) if resolved else None,
            "coverage_rate": round(resolved / total, 4) if total else None,
            "average_error": round(float(avg_return), 6) if avg_return is not None else None,
            "sample_size": directional,
            "first_prediction_time": first_p.isoformat() if first_p else None,
            "latest_prediction_time": latest_p.isoformat() if latest_p else None,
            "latest_resolution_time": latest_r.isoformat() if latest_r else None,
        })
    return out


def outcome_status(ledger: PredictionLedger, resolution: PredictionResolution | None, now: datetime | None = None) -> str:
    """Single source of truth for the dashboard's dot color - green/red/yellow
    only ever apply to a resolved row; unresolved is always gray or orange,
    never green or red."""
    now = now or datetime.now(timezone.utc)
    if resolution is not None:
        if resolution.correct is True:
            return "correct"
        if resolution.correct is False:
            return "wrong"
        return "neutral"
    deadline = ledger.resolution_deadline
    if deadline is not None and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)  # naive SQLite round-trip is always UTC in this codebase
    if deadline and deadline > now:
        return "unresolved_not_due"
    if (ledger.resolver_attempts or 0) > 0:
        return "overdue_provider_error"
    return "unresolved_due"


def latest_results(
    db: Session,
    limit: int = 10,
    symbol: str | None = None,
    timeframe: str | None = None,
    source_name: str | None = None,
    resolved: bool | None = None,
    outcome: str | None = None,
    source_exchange: str | None = None,
) -> list[dict]:
    """Bounded, indexed (generated_at desc) - fetches at most `limit` rows,
    never scans/hydrates the full table."""
    q = (
        db.query(PredictionLedger, PredictionResolution)
        .outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(PredictionLedger.symbol.in_(["BTCUSDT", "ETHUSDT"]))
    )
    if symbol:
        q = q.filter(PredictionLedger.symbol == symbol.upper())
    if timeframe:
        q = q.filter(PredictionLedger.timeframe == timeframe.lower())
    if source_name:
        q = q.filter(PredictionLedger.source_name == source_name)
    if resolved is True:
        q = q.filter(PredictionResolution.id.isnot(None))
    elif resolved is False:
        q = q.filter(PredictionResolution.id.is_(None))
    if source_exchange:
        q = q.filter(PredictionResolution.resolution_exchange == source_exchange)

    q = q.order_by(PredictionLedger.generated_at.desc()).limit(max(1, min(limit, 200)))
    rows = q.all()

    out = []
    for ledger, resolution in rows:
        status = outcome_status(ledger, resolution)
        if outcome and status != outcome:
            continue
        out.append({
            "prediction_id": ledger.prediction_id,
            "symbol": ledger.symbol,
            "direction": ledger.direction,
            "predicted_at": ledger.generated_at.isoformat() if ledger.generated_at else None,
            "due_at": ledger.resolution_deadline.isoformat() if ledger.resolution_deadline else None,
            "resolved_at": resolution.resolved_at.isoformat() if resolution and resolution.resolved_at else None,
            "timeframe": ledger.timeframe,
            "horizon_seconds": ledger.target_horizon_seconds,
            "entry_price": ledger.reference_price,
            "resolved_price": resolution.resolved_price if resolution else None,
            "actual_return": resolution.actual_return if resolution else None,
            "outcome": status,
            "correct": resolution.correct if resolution else None,
            "model_strategy": ledger.source_name,
            "source_type": ledger.source_type,
            "engine": ledger.engine,
            "original_source": "binance_futures",
            "resolution_source": resolution.resolution_provider if resolution else None,
            "resolution_exchange": resolution.resolution_exchange if resolution else None,
            "fallback_used": bool(resolution.fallback_used) if resolution else None,
            "resolution_confidence": resolution.resolution_confidence if resolution else None,
            "unresolved_status": ledger.unresolved_status if not resolution else None,
            "confidence": ledger.confidence,
            "status": "resolved" if resolution else "unresolved",
        })
    return out


def accuracy_summary(db: Session) -> dict:
    """BTC/ETH/combined + per-timeframe/model/strategy/provider accuracy,
    resolved-eligible-only, SQL-aggregated (no full-table hydration)."""
    btc_eth_filter = PredictionLedger.symbol.in_(["BTCUSDT", "ETHUSDT"])
    combined = _accuracy_row(db, None, [btc_eth_filter], combined_key="combined")
    by_symbol = _accuracy_row(db, PredictionLedger.symbol, [btc_eth_filter])
    by_timeframe = _accuracy_row(db, PredictionLedger.timeframe, [btc_eth_filter])
    by_model = _accuracy_row(db, PredictionLedger.source_name, [btc_eth_filter, PredictionLedger.source_type == "ml"])
    by_strategy = _accuracy_row(db, PredictionLedger.source_name, [btc_eth_filter, PredictionLedger.source_type == "strategy"])
    by_engine = _accuracy_row(db, PredictionLedger.engine, [btc_eth_filter])
    by_provider = _accuracy_row(db, PredictionResolution.resolution_provider, [btc_eth_filter])

    return {
        "combined": combined[0] if combined else None,
        "by_symbol": {row["key"]: row for row in by_symbol},
        "by_timeframe": by_timeframe,
        "by_model": by_model,
        "by_strategy": by_strategy,
        "by_engine": by_engine,
        "by_resolution_provider": by_provider,
        "min_sample_for_accuracy": MIN_SAMPLE_FOR_ACCURACY,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
