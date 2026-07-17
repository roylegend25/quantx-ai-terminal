"""Single-resolution ledger worker. It only resolves from stored market candles after deadlines."""
from datetime import datetime, timezone
from app.core.config import settings
from app.db.models import PredictionLedger, PredictionResolution, MarketCandle

def resolve_due(db, limit: int = 200, scan_limit: int | None = None) -> int:
    now = datetime.now(timezone.utc)
    # Legacy gaps must not permanently starve later resolvable predictions.
    # Inspect a bounded superset, while limiting successful writes per cycle.
    scan_limit = scan_limit or max(5000, limit * 25)
    rows = db.query(PredictionLedger).outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id).filter(
        PredictionResolution.id.is_(None), PredictionLedger.resolution_deadline <= now, PredictionLedger.reference_price.isnot(None)
    ).order_by(PredictionLedger.resolution_deadline).limit(scan_limit).all()
    resolved = 0
    for row in rows:
        if resolved >= limit:
            break
        candle = db.query(MarketCandle).filter(MarketCandle.symbol == row.symbol, MarketCandle.timeframe == row.timeframe,
            MarketCandle.timestamp >= int(row.resolution_deadline.timestamp() * 1000)).order_by(MarketCandle.timestamp).first()
        if not candle or not candle.close or not row.reference_price: continue
        actual_return = (float(candle.close) - row.reference_price) / row.reference_price
        path = db.query(MarketCandle).filter(MarketCandle.symbol == row.symbol, MarketCandle.timeframe == row.timeframe,
            MarketCandle.timestamp >= int(row.generated_at.timestamp() * 1000),
            MarketCandle.timestamp <= int(row.resolution_deadline.timestamp() * 1000)).order_by(MarketCandle.timestamp).all()
        highs=[float(item.high) for item in path if item.high is not None]; lows=[float(item.low) for item in path if item.low is not None]
        if row.direction == "LONG":
            mfe=(max(highs)-row.reference_price)/row.reference_price if highs else None; mae=(min(lows)-row.reference_price)/row.reference_price if lows else None
            target_hit=bool(row.target_reference_price is not None and highs and max(highs)>=row.target_reference_price); stop_hit=bool(row.stop_reference_price is not None and lows and min(lows)<=row.stop_reference_price)
        elif row.direction == "SHORT":
            mfe=(row.reference_price-min(lows))/row.reference_price if lows else None; mae=(row.reference_price-max(highs))/row.reference_price if highs else None
            target_hit=bool(row.target_reference_price is not None and lows and min(lows)<=row.target_reference_price); stop_hit=bool(row.stop_reference_price is not None and highs and max(highs)>=row.stop_reference_price)
        else: mfe=mae=None; target_hit=stop_hit=None
        # Symmetric neutral band (see settings.resolution_neutral_band):
        # a move whose magnitude never clears the band is NEUTRAL for both
        # LONG and SHORT predictions, and neutral outcomes stay out of the
        # directional-accuracy denominator (correct stays NULL) instead of
        # counting a sub-noise move as a win or a loss.
        band = abs(settings.resolution_neutral_band)
        actual_direction = "NEUTRAL" if abs(actual_return) <= band else "LONG" if actual_return > 0 else "SHORT"
        correct = None if row.direction not in ("LONG", "SHORT") or actual_direction == "NEUTRAL" else row.direction == actual_direction
        db.add(PredictionResolution(prediction_id=row.prediction_id, actual_return=actual_return, resolved_direction=actual_direction,
            correct=correct, neutral_result=actual_direction == "NEUTRAL", target_hit=target_hit, stop_hit=stop_hit,
            maximum_favorable_excursion=mfe, maximum_adverse_excursion=mae,
            resolution_reason="fixed_horizon_close", resolved_at=now))
        resolved += 1
    db.commit()
    return resolved
