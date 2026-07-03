"""Versioned feature snapshots for the ML lifecycle platform.

Deliberately does not recompute any indicator - it reads whatever the live
path already computed and persisted (PredictionFeature.technical_features
via app/quant/indicators.py, and PredictionFeature.market_context via
app/intelligence/market_intelligence.py) and materializes a versioned,
flattened snapshot for training-time feature comparisons and drift checks.
Independent of app/ml/feature_store.py, which owns the raw per-prediction
log (PredictionFeature) that this module reads from.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import MLOpsFeatureSnapshot, PredictionFeature
from app.db.session import SessionLocal

DEFAULT_FEATURE_VERSION = "features-v1"

# Phase 15 spec field -> where it lives on a PredictionFeature row
TECHNICAL_FIELDS = {
    "rsi": "rsi",
    "macd": "macd",
    "atr": "atr",
    "ema": "ema20",
    "volatility": "realized_volatility",
    "momentum": "macd_hist",
    "trend_score": "trend_score",
}


def _flatten(row: PredictionFeature) -> dict:
    technical = row.technical_features or {}
    context = row.market_context or {}

    features = {key: technical.get(source) for key, source in TECHNICAL_FIELDS.items()}
    features.update(
        {
            "regime": row.feature_regime or technical.get("regime"),
            "orderbook_imbalance": context.get("bid_ask_ratio"),
            "funding": context.get("funding_rate"),
            "open_interest": context.get("open_interest"),
            "volume_profile": context.get("cvd"),
        }
    )
    return features


class FeatureStore:
    def snapshot(
        self,
        symbol: str,
        feature_version: str = DEFAULT_FEATURE_VERSION,
        db: Session | None = None,
    ) -> dict | None:
        """Materializes one MLOpsFeatureSnapshot row from the most recent
        PredictionFeature logged for `symbol`. Returns None if nothing has
        been predicted for that symbol yet."""
        owns_session = db is None
        db = db or SessionLocal()
        try:
            latest = (
                db.query(PredictionFeature)
                .filter(PredictionFeature.symbol == symbol)
                .order_by(PredictionFeature.timestamp.desc())
                .first()
            )
            if latest is None:
                return None

            row = MLOpsFeatureSnapshot(
                feature_version=feature_version,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                features=_flatten(latest),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return self._row_to_dict(row)
        finally:
            if owns_session:
                db.close()

    def snapshot_all_symbols(self, feature_version: str = DEFAULT_FEATURE_VERSION, db: Session | None = None) -> list[dict]:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            symbols = [row[0] for row in db.query(PredictionFeature.symbol).distinct().all()]
            snapshots = []
            for symbol in symbols:
                snap = self.snapshot(symbol, feature_version=feature_version, db=db)
                if snap:
                    snapshots.append(snap)
            return snapshots
        finally:
            if owns_session:
                db.close()

    def get_snapshots(
        self,
        symbol: str | None = None,
        feature_version: str | None = None,
        limit: int = 500,
        db: Session | None = None,
    ) -> list[dict]:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            query = db.query(MLOpsFeatureSnapshot)
            if symbol:
                query = query.filter(MLOpsFeatureSnapshot.symbol == symbol)
            if feature_version:
                query = query.filter(MLOpsFeatureSnapshot.feature_version == feature_version)
            rows = query.order_by(MLOpsFeatureSnapshot.timestamp.desc()).limit(limit).all()
            return [self._row_to_dict(r) for r in rows]
        finally:
            if owns_session:
                db.close()

    @staticmethod
    def _row_to_dict(row: MLOpsFeatureSnapshot) -> dict:
        return {
            "id": row.id,
            "feature_version": row.feature_version,
            "symbol": row.symbol,
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            "features": row.features,
        }


store = FeatureStore()
