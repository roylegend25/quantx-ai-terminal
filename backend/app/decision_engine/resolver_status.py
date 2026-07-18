"""Read-only aggregation for the Prediction Results dashboard tab and
resolver-health API. Every query here is SQL-side aggregation (func.count/
func.sum/GROUP BY) - nothing here hydrates the full prediction_ledger table
into Python. At 150k+ rows and growing, that's the difference between a
sub-100ms dashboard call and a multi-second one holding a DB connection
while it walks every row.

Reads app.decision_engine.resolver's structured `unresolved_reason` values
directly (Phase 33) rather than inventing a parallel status vocabulary.
"""
from datetime import datetime, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.data_sources import symbol_map
from app.db.models import PredictionLedger, PredictionResolution
from app.decision_engine import scheduler as resolver_scheduler

MIN_SAMPLE_FOR_ACCURACY = 20
CANONICAL_SYMBOLS = ("BTCUSDT", "ETHUSDT")

# Every reason app.decision_engine.resolver actually persists onto
# PredictionLedger.unresolved_reason, plus the two lazily-computed ones
# (awaiting_horizon / due_for_resolution) this module fills in for rows a
# resolver cycle hasn't touched yet.
UNRESOLVED_REASONS = (
    "awaiting_horizon", "due_for_resolution", "awaiting_future_candle", "market_data_gap",
    "provider_unavailable", "exchange_price_disagreement", "resolver_delayed", "resolver_error",
    "permanent_data_gap", "unsupported_timeframe", "invalid_due_time", "missing_entry_price",
)


def unresolved_reason_summary(db: Session, symbol: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    q = (
        db.query(PredictionLedger.symbol, PredictionLedger.unresolved_reason, func.count(PredictionLedger.prediction_id))
        .outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(PredictionResolution.id.is_(None))
    )
    if symbol:
        q = q.filter(PredictionLedger.symbol == symbol.upper())
    q = q.group_by(PredictionLedger.symbol, PredictionLedger.unresolved_reason)

    out: dict[str, dict[str, int]] = {}
    for sym, reason, count in q.all():
        reason = reason or "due_for_resolution"  # never-touched rows: NULL until a resolver cycle sees them
        out.setdefault(sym, {}).setdefault(reason, 0)
        out[sym][reason] += count

    # Split the "due_for_resolution" bucket into rows genuinely not due yet
    # vs rows that are due but a cycle hasn't reached them - unresolved_reason
    # alone can't tell them apart since resolve_due's query already excludes
    # not-due rows (so they never get a reason written at all).
    not_due_q = (
        db.query(PredictionLedger.symbol, func.count(PredictionLedger.prediction_id))
        .outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(PredictionResolution.id.is_(None), PredictionLedger.unresolved_reason.is_(None),
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

    return {"symbols": out, "reasons": list(UNRESOLVED_REASONS), "generated_at": now.isoformat()}


def catchup_progress(db: Session) -> dict:
    now = datetime.now(timezone.utc)
    base = db.query(PredictionLedger).outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id).filter(PredictionResolution.id.is_(None))
    total_due = base.filter(PredictionLedger.resolution_deadline <= now).count()
    total_overdue = base.filter(PredictionLedger.resolution_deadline <= now, PredictionLedger.resolver_attempts > 0).count()

    per_symbol = {}
    for sym in CANONICAL_SYMBOLS:
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
    oldest_dt = oldest[0] if oldest else None
    if oldest_dt is not None and oldest_dt.tzinfo is None:
        oldest_dt = oldest_dt.replace(tzinfo=timezone.utc)
    oldest_age_seconds = (now - oldest_dt).total_seconds() if oldest_dt else None

    # Recent-resolution provider mix, live from the DB rather than in-memory
    # per-cycle counters - survives restarts and doesn't need scheduler.STATUS changes.
    since = now.replace(microsecond=0) - __import__("datetime").timedelta(hours=1)
    recent = (
        db.query(func.sum(case((PredictionResolution.fallback_used.is_(True), 1), else_=0)),
                  func.sum(case((PredictionResolution.fallback_used.is_(False), 1), else_=0)))
        .filter(PredictionResolution.resolved_at >= since, PredictionResolution.prediction_id.in_(
            db.query(PredictionLedger.prediction_id).filter(PredictionLedger.symbol.in_(CANONICAL_SYMBOLS))
        )).one()
    )
    fallback_count, primary_count = (recent[0] or 0), (recent[1] or 0)

    status = resolver_scheduler.status()
    return {
        "total_due": total_due,
        "total_overdue": total_overdue,
        "btc": per_symbol["BTCUSDT"],
        "eth": per_symbol["ETHUSDT"],
        "resolved_last_cycle": status.get("last_resolved", 0),
        "primary_source_resolutions_last_hour": int(primary_count),
        "fallback_resolutions_last_hour": int(fallback_count),
        "oldest_overdue_age_seconds": oldest_age_seconds,
        "last_run": status.get("last_run"),
        "last_success": status.get("last_success"),
        "last_error": status.get("last_error"),
        "scheduler_running": status.get("running", False),
    }


def provider_health() -> dict:
    """Static capability/config report - not a live ping (avoids spending
    rate-limit budget just to render a health badge)."""
    return {
        "providers": [
            {"provider": "binance_futures", "role": "primary", "market_type": "usdt_perp", "enabled": True},
            {"provider": "bybit", "role": "fallback", "market_type": "usdt_perp", "enabled": True},
            {"provider": "okx", "role": "fallback", "market_type": "usdt_swap", "enabled": True},
            {"provider": "hyperliquid", "role": "fallback", "market_type": "usdt_perp", "enabled": True},
            {"provider": "binance_spot", "role": "fallback", "market_type": "spot", "enabled": False},
        ],
        "canonical_symbols": list(CANONICAL_SYMBOLS),
    }


def outcome_status(ledger: PredictionLedger, resolution: PredictionResolution | None, now: datetime | None = None) -> str:
    """Single source of truth for the dashboard's dot color - green/red/
    yellow only ever apply to a resolved row; unresolved is always gray or
    orange, never green or red."""
    now = now or datetime.now(timezone.utc)
    if resolution is not None:
        if resolution.correct is True:
            return "correct"
        if resolution.correct is False:
            return "wrong"
        return "neutral"
    deadline = ledger.resolution_deadline
    if deadline is not None and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
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
        .filter(PredictionLedger.symbol.in_(CANONICAL_SYMBOLS))
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
            "unresolved_reason": ledger.unresolved_reason if not resolution else None,
            "confidence": ledger.confidence,
            "status": "resolved" if resolution else "unresolved",
        })
    return out


def _accuracy_row(db: Session, group_col, filters: list, combined_key: str | None = None) -> list[dict]:
    """group_col=None aggregates everything into one row (the "combined"
    summary, labeled `combined_key` in Python) instead of GROUP BY."""
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


def accuracy_summary(db: Session) -> dict:
    """BTC/ETH/combined + per-timeframe/model/strategy/provider accuracy,
    resolved-eligible-only, SQL-aggregated (no full-table hydration)."""
    btc_eth_filter = PredictionLedger.symbol.in_(CANONICAL_SYMBOLS)
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
