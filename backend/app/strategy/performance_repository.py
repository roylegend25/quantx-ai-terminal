import json
import statistics
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import StrategyPerformance
from app.db.session import SessionLocal

STRATEGY_NAMES = ["trend", "momentum", "mean_reversion", "breakout"]
ROLLING_WINDOW = 20
DEFAULT_WEIGHT = round(1.0 / len(STRATEGY_NAMES), 6)

NEUTRAL_STATS = {
    "rolling_win_rate": 50.0,
    "average_r_multiple": 0.0,
    "profit_factor": 1.0,
    "sharpe_ratio": 0.0,
    "max_drawdown": 0.0,
}


def _compute_stats(trades: list) -> dict:
    if not trades:
        return {
            "rolling_win_rate": 0.0,
            "average_r_multiple": 0.0,
            "profit_factor": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "average_confidence": 0.0,
            "regime_performance": {},
        }

    r_values = [t["r_multiple"] for t in trades]
    wins = [t for t in trades if t["win"]]
    confidences = [t["confidence"] for t in trades]

    rolling_win_rate = round(len(wins) / len(trades) * 100, 2)
    average_r_multiple = round(statistics.mean(r_values), 4)
    average_confidence = round(statistics.mean(confidences), 2)

    gross_profit = sum(r for r in r_values if r > 0)
    gross_loss = abs(sum(r for r in r_values if r < 0))
    if gross_loss > 0:
        profit_factor = round(min(gross_profit / gross_loss, 99.0), 4)
    else:
        profit_factor = 99.0 if gross_profit > 0 else 0.0

    if len(r_values) >= 2 and statistics.pstdev(r_values) > 0:
        sharpe_ratio = round(statistics.mean(r_values) / statistics.pstdev(r_values), 4)
    else:
        sharpe_ratio = 0.0

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for r in r_values:
        cumulative += r
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    max_drawdown = round(max_drawdown, 4)

    regime_performance: dict = {}
    for t in trades:
        regime = t.get("regime") or "UNKNOWN"
        bucket = regime_performance.setdefault(regime, {"trades": 0, "wins": 0})
        bucket["trades"] += 1
        if t["win"]:
            bucket["wins"] += 1
    for bucket in regime_performance.values():
        bucket["win_rate"] = round(bucket["wins"] / bucket["trades"] * 100, 2)

    return {
        "rolling_win_rate": rolling_win_rate,
        "average_r_multiple": average_r_multiple,
        "profit_factor": profit_factor,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_drawdown,
        "average_confidence": average_confidence,
        "regime_performance": regime_performance,
    }


def _score(stats: dict) -> float:
    win_rate = stats["rolling_win_rate"] / 100.0
    r_component = max(0.0, stats["average_r_multiple"])
    r_component = r_component / (1 + r_component)
    pf_component = min(stats["profit_factor"], 3.0) / 3.0
    sharpe_component = max(0.0, min(stats["sharpe_ratio"], 3.0)) / 3.0
    dd_penalty = 1.0 / (1.0 + max(0.0, stats["max_drawdown"]))

    score = (
        0.35 * win_rate
        + 0.30 * r_component
        + 0.20 * pf_component
        + 0.15 * sharpe_component
    ) * dd_penalty

    return max(score, 0.0001)


def _row_to_dict(row: StrategyPerformance) -> dict:
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


class StrategyPerformanceRepository:
    """Persists rolling per-strategy performance and adaptive ensemble weights in SQLite."""

    def _get_or_create(self, db: Session, name: str) -> StrategyPerformance:
        row = db.get(StrategyPerformance, name)
        if row is None:
            row = StrategyPerformance(
                strategy_name=name,
                trades=0,
                wins=0,
                losses=0,
                current_weight=DEFAULT_WEIGHT,
                trades_json="[]",
                regime_performance_json="{}",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    def seed_defaults(self, db: Session | None = None):
        """Ensure every known strategy has a row, each starting at weight 0.25."""
        owns_session = db is None
        db = db or SessionLocal()
        try:
            for name in STRATEGY_NAMES:
                self._get_or_create(db, name)
        finally:
            if owns_session:
                db.close()

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
        """Upsert arbitrary fields on a strategy's performance row."""
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

    def _recompute_weights(self, db: Session):
        rows = {name: self._get_or_create(db, name) for name in STRATEGY_NAMES}

        scores = {}
        for name, row in rows.items():
            trades = json.loads(row.trades_json or "[]")
            if not trades:
                # no evidence yet: assume breakeven performance rather than
                # inheriting the average of whichever strategies do have data
                scores[name] = _score(NEUTRAL_STATS)
                continue
            scores[name] = _score(_compute_stats(trades))

        total = sum(scores.values()) or 1.0
        for name, row in rows.items():
            row.current_weight = round(scores[name] / total, 6)

        db.commit()

    def update_metrics(
        self,
        name: str,
        *,
        r_multiple: float,
        win: bool,
        confidence: float,
        regime: str | None,
        db: Session | None = None,
    ) -> dict:
        """Append a trade outcome to a strategy's rolling window, recompute its
        metrics, and re-normalize every strategy's current_weight."""
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

            self._recompute_weights(db)
            db.refresh(row)
            return _row_to_dict(row)
        finally:
            if owns_session:
                db.close()

    def get_weights(self, db: Session | None = None) -> dict:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            return {
                name: self._get_or_create(db, name).current_weight
                for name in STRATEGY_NAMES
            }
        finally:
            if owns_session:
                db.close()

    def reset_all(self, db: Session | None = None) -> None:
        """Wipe every strategy's rolling trade window back to a fresh,
        equal-weight state. Used by POST /api/paper/reset - these stats are
        derived entirely from closed paper trades, so they'd otherwise keep
        scoring strategies against trades that no longer exist."""
        owns_session = db is None
        db = db or SessionLocal()
        try:
            for name in STRATEGY_NAMES:
                row = self._get_or_create(db, name)
                row.trades = 0
                row.wins = 0
                row.losses = 0
                row.rolling_win_rate = 0.0
                row.average_r_multiple = 0.0
                row.profit_factor = 0.0
                row.sharpe_ratio = 0.0
                row.max_drawdown = 0.0
                row.average_confidence = 0.0
                row.current_weight = DEFAULT_WEIGHT
                row.trades_json = "[]"
                row.regime_performance_json = "{}"
                row.updated_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            if owns_session:
                db.close()


repository = StrategyPerformanceRepository()
