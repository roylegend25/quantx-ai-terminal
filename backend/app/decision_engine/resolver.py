"""Single-resolution ledger worker. It only resolves from stored market candles after deadlines."""
from datetime import datetime, timezone
from app.db.models import PredictionLedger, PredictionResolution, MarketCandle

def resolve_due(db, limit: int = 200) -> int:
    now = datetime.now(timezone.utc)
    rows = db.query(PredictionLedger).outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id).filter(
        PredictionResolution.id.is_(None), PredictionLedger.resolution_deadline <= now, PredictionLedger.reference_price.isnot(None)
    ).order_by(PredictionLedger.resolution_deadline).limit(limit).all()
    resolved = 0
    for row in rows:
        candle = db.query(MarketCandle).filter(MarketCandle.symbol == row.symbol, MarketCandle.timeframe == row.timeframe,
            MarketCandle.timestamp >= int(row.resolution_deadline.timestamp() * 1000)).order_by(MarketCandle.timestamp).first()
        if not candle or not candle.close or not row.reference_price: continue
        actual_return = (float(candle.close) - row.reference_price) / row.reference_price
        actual_direction = "LONG" if actual_return > 0 else "SHORT" if actual_return < 0 else "NEUTRAL"
        correct = None if row.direction not in ("LONG", "SHORT") else row.direction == actual_direction
        db.add(PredictionResolution(prediction_id=row.prediction_id, actual_return=actual_return, resolved_direction=actual_direction,
            correct=correct, neutral_result=actual_direction == "NEUTRAL", resolution_reason="fixed_horizon_close", resolved_at=now))
        resolved += 1
    db.commit()
    return resolved
