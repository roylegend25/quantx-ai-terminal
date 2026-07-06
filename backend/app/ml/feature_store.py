from sqlalchemy.orm import Session

from app.db.models import PredictionFeature
from app.db.session import SessionLocal


def _build_decision_reason(prediction: dict) -> str:
    direction = prediction.get("direction")
    strategies = prediction.get("strategies") or {}

    supporting = [
        f"{name}: {result.get('reason')}"
        for name, result in strategies.items()
        if result.get("direction") == direction and result.get("reason")
    ]
    if supporting:
        return "; ".join(supporting)

    return f"Ensemble direction {direction} at confidence {prediction.get('confidence')}"


class FeatureStore:
    """Persists every prediction's full feature vector, and later back-fills
    it with the outcome of whichever paper trade (if any) acted on it."""

    def save_prediction(
        self,
        *,
        symbol: str,
        timeframe: str,
        prediction: dict,
        latency_ms: float | None = None,
        db: Session | None = None,
    ) -> int:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = PredictionFeature(
                latency_ms=latency_ms,
                symbol=symbol,
                timeframe=timeframe,
                direction=prediction.get("direction"),
                confidence=prediction.get("confidence"),
                regime=prediction.get("regime"),
                feature_regime=prediction.get("feature_regime"),
                adaptive_strategy_weights=prediction.get("strategy_weights"),
                market_context=prediction.get("market_context"),
                multi_timeframe_consensus=prediction.get("multi_timeframe_consensus"),
                technical_features=prediction.get("features"),
                decision_reason=_build_decision_reason(prediction),
                entry_price=prediction.get("price"),
                target=prediction.get("target"),
                stop=prediction.get("stop"),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
        finally:
            if owns_session:
                db.close()

    def record_outcome(
        self,
        feature_id: int,
        *,
        exit_price: float,
        pnl: float,
        db: Session | None = None,
    ) -> PredictionFeature | None:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = db.get(PredictionFeature, feature_id)
            if row is None:
                return None

            row.exit_price = exit_price
            row.pnl = pnl
            row.outcome = "WIN" if pnl >= 0 else "LOSS"

            if row.entry_price:
                if row.direction == "SHORT":
                    row.realized_return = round((row.entry_price - exit_price) / row.entry_price, 6)
                else:
                    row.realized_return = round((exit_price - row.entry_price) / row.entry_price, 6)
            else:
                row.realized_return = None

            db.commit()
            db.refresh(row)
            return row
        finally:
            if owns_session:
                db.close()

    def get_dataset_info(self, db: Session | None = None) -> dict:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            rows = db.query(PredictionFeature).all()

            feature_count = 0
            for row in reversed(rows):
                if row.technical_features:
                    feature_count = len(row.technical_features)
                    break

            return {
                "samples": len(rows),
                "wins": sum(1 for r in rows if r.outcome == "WIN"),
                "losses": sum(1 for r in rows if r.outcome == "LOSS"),
                "feature_count": feature_count,
            }
        finally:
            if owns_session:
                db.close()


store = FeatureStore()
