import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import StrategyRollingMetrics
from app.db.session import SessionLocal
from app.strategy.performance_repository import ROLLING_WINDOW, STRATEGY_NAMES, _compute_stats


def _row_to_dict(row: StrategyRollingMetrics) -> dict:
    return {
        "strategy_name": row.strategy_name,
        "trades": row.trades,
        "wins": row.wins,
        "losses": row.losses,
        "rolling_win_rate": row.rolling_win_rate,
        "average_r_multiple": row.average_r_multiple,
        "profit_factor": row.profit_factor,
        "sharpe_ratio": row.sharpe_ratio,
        "max_drawdown": row.max_drawdown,
        "average_confidence": row.average_confidence,
        "current_weight": row.current_weight,
        "regime_performance": json.loads(row.regime_performance_json or "{}"),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class StrategyRollingMetricsRepository:
    """Tracks rolling last-20-trade performance stats per strategy, independently
    of the production StrategyPerformance/current_weight pipeline that ensemble.py
    consumes today. Intended as a staging ground for future weighting logic -
    recording here never changes any live prediction or weight.
    """

    def _get_or_create(self, db: Session, name: str) -> StrategyRollingMetrics:
        row = db.get(StrategyRollingMetrics, name)
        if row is None:
            row = StrategyRollingMetrics(
                strategy_name=name,
                trades_json="[]",
                regime_performance_json="{}",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    def get_all(self, db: Session | None = None) -> dict:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            return {
                name: _row_to_dict(self._get_or_create(db, name))
                for name in STRATEGY_NAMES
            }
        finally:
            if owns_session:
                db.close()

    def get(self, name: str, db: Session | None = None) -> dict | None:
        if name not in STRATEGY_NAMES:
            return None
        owns_session = db is None
        db = db or SessionLocal()
        try:
            return _row_to_dict(self._get_or_create(db, name))
        finally:
            if owns_session:
                db.close()

    def save(self, name: str, db: Session | None = None, **fields) -> dict:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = self._get_or_create(db, name)
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(row)
            return _row_to_dict(row)
        finally:
            if owns_session:
                db.close()

    def record_trade(
        self,
        name: str,
        *,
        r_multiple: float,
        win: bool,
        confidence: float,
        regime: str | None,
        db: Session | None = None,
    ) -> dict:
        """Append a trade outcome to the rolling window and recompute this
        strategy's stats. Does not touch StrategyPerformance.current_weight."""
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = self._get_or_create(db, name)
            trades = json.loads(row.trades_json or "[]")
            trades.append({
                "r_multiple": round(float(r_multiple), 4),
                "win": bool(win),
                "confidence": round(float(confidence or 0), 2),
                "regime": regime or "UNKNOWN",
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            trades = trades[-ROLLING_WINDOW:]
            row.trades_json = json.dumps(trades)

            stats = _compute_stats(trades)
            row.trades = len(trades)
            row.wins = sum(1 for t in trades if t["win"])
            row.losses = sum(1 for t in trades if not t["win"])
            row.rolling_win_rate = stats["rolling_win_rate"]
            row.average_r_multiple = stats["average_r_multiple"]
            row.profit_factor = stats["profit_factor"]
            row.sharpe_ratio = stats["sharpe_ratio"]
            row.max_drawdown = stats["max_drawdown"]
            row.average_confidence = stats["average_confidence"]
            row.regime_performance_json = json.dumps(stats["regime_performance"])
            row.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(row)
            return _row_to_dict(row)
        finally:
            if owns_session:
                db.close()


repository = StrategyRollingMetricsRepository()
