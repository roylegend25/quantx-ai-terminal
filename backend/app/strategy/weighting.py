import json
import statistics
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import StrategyWeight
from app.db.session import SessionLocal

STRATEGY_NAMES = ["trend", "momentum", "mean_reversion", "breakout"]
ROLLING_WINDOW = 20
DEFAULT_WEIGHT = round(1.0 / len(STRATEGY_NAMES), 6)


def _get_or_create(db: Session, name: str) -> StrategyWeight:
    row = db.get(StrategyWeight, name)
    if row is None:
        row = StrategyWeight(
            strategy=name,
            weight=DEFAULT_WEIGHT,
            trades_json="[]",
            regime_performance_json="{}",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _compute_stats(trades: list) -> dict:
    if not trades:
        return {
            "win_rate": 0.0,
            "avg_r_multiple": 0.0,
            "profit_factor": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "avg_confidence": 0.0,
            "regime_performance": {},
        }

    r_values = [t["r_multiple"] for t in trades]
    wins = [t for t in trades if t["win"]]
    confidences = [t["confidence"] for t in trades]

    win_rate = round(len(wins) / len(trades) * 100, 2)
    avg_r_multiple = round(statistics.mean(r_values), 4)
    avg_confidence = round(statistics.mean(confidences), 2)

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
        "win_rate": win_rate,
        "avg_r_multiple": avg_r_multiple,
        "profit_factor": profit_factor,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_drawdown,
        "avg_confidence": avg_confidence,
        "regime_performance": regime_performance,
    }


NEUTRAL_STATS = {
    "win_rate": 50.0,
    "avg_r_multiple": 0.0,
    "profit_factor": 1.0,
    "sharpe_ratio": 0.0,
    "max_drawdown": 0.0,
}


def _score(stats: dict) -> float:
    win_rate = stats["win_rate"] / 100.0
    r_component = max(0.0, stats["avg_r_multiple"])
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


def _recompute_weights(db: Session):
    rows = {name: _get_or_create(db, name) for name in STRATEGY_NAMES}

    scores = {}
    for name, row in rows.items():
        trades = json.loads(row.trades_json or "[]")
        if not trades:
            # no evidence yet: assume breakeven performance rather than
            # inheriting the average of whichever strategies do have data
            scores[name] = _score(NEUTRAL_STATS)
            continue
        stats = _compute_stats(trades)
        scores[name] = _score(stats)

    total = sum(scores.values()) or 1.0
    for name, row in rows.items():
        row.weight = round(scores[name] / total, 6)

    db.commit()


def record_trade_result(
    strategy: str,
    *,
    r_multiple: float,
    win: bool,
    confidence: float,
    regime: str | None,
    db: Session | None = None,
):
    """Append a trade outcome to a strategy's rolling window and recompute all weights."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        row = _get_or_create(db, strategy)
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
        row.win_rate = stats["win_rate"]
        row.avg_r_multiple = stats["avg_r_multiple"]
        row.profit_factor = stats["profit_factor"]
        row.sharpe_ratio = stats["sharpe_ratio"]
        row.max_drawdown = stats["max_drawdown"]
        row.avg_confidence = stats["avg_confidence"]
        row.regime_performance_json = json.dumps(stats["regime_performance"])
        row.updated_at = datetime.now(timezone.utc)
        db.commit()

        _recompute_weights(db)
    finally:
        if owns_session:
            db.close()


def get_weights(db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        return {name: _get_or_create(db, name).weight for name in STRATEGY_NAMES}
    finally:
        if owns_session:
            db.close()


def get_stats(db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        result = {}
        for name in STRATEGY_NAMES:
            row = _get_or_create(db, name)
            trades = json.loads(row.trades_json or "[]")
            result[name] = {
                "weight": row.weight,
                "trade_count": len(trades),
                "win_rate": row.win_rate,
                "avg_r_multiple": row.avg_r_multiple,
                "profit_factor": row.profit_factor,
                "sharpe_ratio": row.sharpe_ratio,
                "max_drawdown": row.max_drawdown,
                "avg_confidence": row.avg_confidence,
                "regime_performance": json.loads(row.regime_performance_json or "{}"),
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        return result
    finally:
        if owns_session:
            db.close()
