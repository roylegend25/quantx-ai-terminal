"""Read-only aggregation for the resolver/accuracy dashboard surfaces.

Every query here is SQL-side aggregation (func.count/func.sum/GROUP BY) - no
endpoint in this module ever hydrates the full prediction_ledger table into
Python. At 150k+ rows and growing, that distinction is the difference
between a sub-100ms dashboard call and a multi-second one holding a DB
connection while it walks every row.
"""
from datetime import datetime, timezone

from sqlalchemy import case, func, or_
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
    now = datetime.now(timezone.utc)
    base = db.query(PredictionLedger).outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id).filter(PredictionResolution.id.is_(None))
    total_due = base.filter(PredictionLedger.resolution_deadline <= now).count()
    total_overdue = base.filter(PredictionLedger.resolution_deadline <= now, PredictionLedger.resolver_attempts > 0).count()
    processed = db.query(func.count(PredictionLedger.prediction_id)).filter(
        PredictionLedger.symbol.in_(("BTCUSDT", "ETHUSDT")), PredictionLedger.resolver_attempts > 0
    ).scalar() or 0
    resolved_total = db.query(func.count(PredictionResolution.id)).join(
        PredictionLedger, PredictionResolution.prediction_id == PredictionLedger.prediction_id
    ).filter(PredictionLedger.symbol.in_(("BTCUSDT", "ETHUSDT"))).scalar() or 0
    delayed = base.filter(PredictionLedger.symbol.in_(("BTCUSDT", "ETHUSDT")),
                          PredictionLedger.unresolved_status.in_(("resolver_delayed", "secondary_provider_pending",
                                                                  "primary_provider_unavailable", "primary_market_data_gap"))).count()
    permanently_failed = base.filter(PredictionLedger.symbol.in_(("BTCUSDT", "ETHUSDT")),
                                     PredictionLedger.unresolved_status == "permanent_data_gap").count()

    per_symbol = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        sym_base = base.filter(PredictionLedger.symbol == sym)
        per_symbol[sym] = {
            "due": sym_base.filter(PredictionLedger.resolution_deadline <= now).count(),
            "overdue": sym_base.filter(PredictionLedger.resolution_deadline <= now, PredictionLedger.resolver_attempts > 0).count(),
        }

    oldest = (
        base.filter(PredictionLedger.resolution_deadline <= now)
        .order_by(PredictionLedger.resolution_deadline)
        .with_entities(PredictionLedger.resolution_deadline)
        .first()
    )
    oldest_age_seconds = (now - oldest[0].replace(tzinfo=timezone.utc)).total_seconds() if oldest and oldest[0] else None

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
    directly observable rather than inferred."""
    now = datetime.now(timezone.utc)
    btc_eth = PredictionLedger.symbol.in_(("BTCUSDT", "ETHUSDT"))

    counts = dict(
        db.query(PredictionLedger.lifecycle_status, func.count(PredictionLedger.prediction_id))
        .filter(btc_eth).group_by(PredictionLedger.lifecycle_status).all()
    )
    # Rows still stored as PENDING (or NULL, pre-migration legacy rows) whose
    # deadline has actually passed are live-derived as RESOLVING for
    # display, exactly as outcome.effective_lifecycle_status does per-row -
    # done here as one aggregate query instead of a python loop over 200k+ rows.
    stored_pending_matured = (
        db.query(func.count(PredictionLedger.prediction_id))
        .filter(btc_eth, PredictionLedger.resolution_deadline <= now,
                or_(PredictionLedger.lifecycle_status.is_(None), PredictionLedger.lifecycle_status == outcome.PENDING))
        .scalar() or 0
    )
    stored_pending_not_matured = (
        db.query(func.count(PredictionLedger.prediction_id))
        .filter(btc_eth, PredictionLedger.resolution_deadline > now,
                or_(PredictionLedger.lifecycle_status.is_(None), PredictionLedger.lifecycle_status == outcome.PENDING))
        .scalar() or 0
    )
    display_counts = {k: v for k, v in counts.items() if k not in (None, outcome.PENDING)}
    display_counts[outcome.PENDING] = stored_pending_not_matured
    display_counts[outcome.RESOLVING] = display_counts.get(outcome.RESOLVING, 0) + stored_pending_matured

    oldest_pending = (
        db.query(PredictionLedger.resolution_deadline)
        .filter(btc_eth, PredictionLedger.resolution_deadline <= now,
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
