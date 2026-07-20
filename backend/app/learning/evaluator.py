"""Safe auto-learning pipeline, step 1-6 of the Phase 20 loop: collect
stored predictions, resolve them against *recorded* outcomes, and aggregate
performance statistics.

Resolution sources, in order of trust:
1. The paper trade that acted on the prediction (exit_price/outcome already
   back-filled on the PredictionFeature row).
2. The actual later close from the validated market_candles store (the
   candle one full timeframe-interval after the prediction).
3. The next prediction's observed entry price in the same series (the same
   fallback GET /api/prediction/history uses).

Nothing is interpolated or invented; a prediction with no recorded later
price stays unresolved. Results are stored as LearningEvaluation snapshots -
this module never mutates PredictionFeature rows, live strategy weights or
any model.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.timeframes.canonical import next_month_boundary_ms, parse_timeframe, timeframe_capabilities
from app.db.models import LearningEvaluation, MarketCandle, PredictionFeature
from app.db.session import SessionLocal

CONFIDENCE_BUCKETS = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]


def _to_ms(dt) -> int | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _close_after(db: Session, symbol: str, timeframe: str, after_ms: int) -> float | None:
    """Close of the first stored real (non-interpolated) candle whose open
    time is at or after after_ms."""
    row = (
        db.query(MarketCandle.close)
        .filter(
            MarketCandle.symbol == symbol,
            MarketCandle.timeframe == timeframe,
            MarketCandle.timestamp >= after_ms,
            MarketCandle.interpolated.is_(False),
        )
        .order_by(MarketCandle.timestamp.asc())
        .first()
    )
    return float(row[0]) if row else None


def _bucket_label(lo: int, hi: int) -> str:
    return f"{lo}-{min(hi, 100)}"


def resolve_predictions(rows: list[PredictionFeature], db: Session) -> list[dict]:
    """One evaluation record per directional prediction; non-directional
    (NO_TRADE) rows are excluded from accuracy but counted by the caller."""
    out = []
    for i, row in enumerate(rows):
        if row.direction not in ("LONG", "SHORT") or row.entry_price is None:
            continue

        source = None
        actual = None
        if row.outcome in ("WIN", "LOSS") and row.exit_price is not None:
            actual, source = row.exit_price, "paper_trade"
        else:
            ts_ms = _to_ms(row.timestamp)
            try:
                canonical = parse_timeframe(row.timeframe).value
                metadata = timeframe_capabilities(canonical)
            except ValueError:
                canonical, metadata = row.timeframe, None
            resolution_ms = (next_month_boundary_ms(ts_ms) if metadata and metadata.kind == "calendar" and ts_ms
                             else ts_ms + metadata.fixed_duration_ms if metadata and ts_ms else None)
            if resolution_ms:
                actual = _close_after(db, row.symbol, canonical, resolution_ms)
                if actual is not None:
                    source = "market_candles"
            if actual is None and i + 1 < len(rows) and rows[i + 1].entry_price is not None:
                actual, source = rows[i + 1].entry_price, "next_prediction"

        if actual is None:
            continue

        moved_up = actual > row.entry_price
        correct = moved_up == (row.direction == "LONG")
        predicted_price = row.target if row.target else None
        error_pct = (
            round(abs(actual - predicted_price) / actual * 100, 4) if predicted_price and actual else None
        )

        out.append(
            {
                "id": row.id,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "direction": row.direction,
                "confidence": row.confidence,
                "regime": row.regime,
                "correct": correct,
                "error_pct": error_pct,
                "realized_return_pct": round((actual - row.entry_price) / row.entry_price * 100, 4),
                "source": source,
            }
        )
    return out


def _group_stats(records: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(str(r.get(key) or "unknown"), []).append(r)
    return {
        name: {
            "predictions": len(rs),
            "hit_rate_pct": round(sum(1 for r in rs if r["correct"]) / len(rs) * 100, 2),
            "avg_error_pct": _avg([r["error_pct"] for r in rs if r["error_pct"] is not None]),
            "avg_confidence": _avg([r["confidence"] for r in rs if r["confidence"] is not None]),
        }
        for name, rs in groups.items()
    }


def _avg(values: list) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _calibration(records: list[dict]) -> list[dict]:
    """Confidence reliability: within each stated-confidence bucket, how
    often was the direction actually right? A well-calibrated model's
    hit_rate tracks its bucket."""
    buckets = []
    for lo, hi in CONFIDENCE_BUCKETS:
        rs = [r for r in records if r["confidence"] is not None and lo <= r["confidence"] < hi]
        buckets.append(
            {
                "bucket": _bucket_label(lo, hi),
                "predictions": len(rs),
                "hit_rate_pct": round(sum(1 for r in rs if r["correct"]) / len(rs) * 100, 2) if rs else None,
                "avg_confidence": _avg([r["confidence"] for r in rs]),
            }
        )
    return buckets


def evaluate(symbol: str | None = None, limit: int = 2000, db: Session | None = None) -> dict:
    owns = db is None
    db = db or SessionLocal()
    try:
        q = db.query(PredictionFeature)
        if symbol:
            q = q.filter(PredictionFeature.symbol == symbol.upper())
        rows = q.order_by(PredictionFeature.timestamp.desc()).limit(limit).all()
        rows.reverse()

        records = resolve_predictions(rows, db)
        hit_rate = (
            round(sum(1 for r in records if r["correct"]) / len(records) * 100, 2) if records else None
        )

        snapshot = LearningEvaluation(
            symbol=symbol.upper() if symbol else None,
            predictions_considered=len(rows),
            predictions_resolved=len(records),
            direction_hit_rate_pct=hit_rate,
            avg_error_pct=_avg([r["error_pct"] for r in records if r["error_pct"] is not None]),
            avg_confidence=_avg([r["confidence"] for r in records if r["confidence"] is not None]),
            confidence_reliability=_calibration(records),
            by_timeframe=_group_stats(records, "timeframe"),
            by_regime=_group_stats(records, "regime"),
            by_direction=_group_stats(records, "direction"),
            details={
                "resolution_sources": _group_stats(records, "source"),
                "note": "resolved against paper-trade exits, stored market candles, or the next prediction's observed price - never interpolated",
            },
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)

        return latest_performance(db=db)
    finally:
        if owns:
            db.close()


def latest_performance(db: Session | None = None) -> dict:
    owns = db is None
    db = db or SessionLocal()
    try:
        row = db.query(LearningEvaluation).order_by(LearningEvaluation.evaluated_at.desc()).first()
        if row is None:
            return {
                "evaluated_at": None,
                "note": "No evaluation run yet - POST /api/learning/evaluate first",
            }
        return {
            "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
            "symbol": row.symbol,
            "predictions_considered": row.predictions_considered,
            "predictions_resolved": row.predictions_resolved,
            "direction_hit_rate_pct": row.direction_hit_rate_pct,
            "avg_error_pct": row.avg_error_pct,
            "avg_confidence": row.avg_confidence,
            "confidence_reliability": row.confidence_reliability,
            "by_timeframe": row.by_timeframe,
            "by_regime": row.by_regime,
            "by_direction": row.by_direction,
            "details": row.details,
        }
    finally:
        if owns:
            db.close()
