"""Steps 7-10 of the Phase 20 learning loop: strategy weight review,
challenger training, and promotion *recommendations*.

Deliberately advisory: the only side effects are (a) shadow candidate
weights written to StrategyRollingMetrics (never read by the live ensemble)
and (b) challenger training jobs queued through the existing AI-lab job
runner - which itself only ever produces Challenger-status models. Nothing
here promotes a model or changes a live weight; promotion stays behind the
existing threshold-gated /api/ml/promote path.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models import (
    DataQualityReport,
    LearningEvaluation,
    MLOpsDriftRecord,
    MLOpsModel,
)
from app.db.session import SessionLocal
from app.ml_lab import jobs as ml_jobs
from app.strategy import weight_calculator
from app.strategy.performance_repository import repository as performance_repository
from app.strategy.rolling_metrics_repository import repository as rolling_metrics_repository

MIN_SAMPLES_FOR_JUDGMENT = 30
WEAK_TIMEFRAME_HIT_RATE = 48.0
DATA_QUALITY_FLOOR = 70.0


def strategy_weights(db: Session | None = None) -> dict:
    """Live weights (what the ensemble actually uses) side by side with the
    shadow candidate weights recomputed from proven rolling performance."""
    owns = db is None
    db = db or SessionLocal()
    try:
        live = performance_repository.get_all(db=db)
        candidate = weight_calculator.recompute_and_store(db=db)
        shadow_stats = rolling_metrics_repository.get_all(db=db)
        return {
            "live_weights": {name: stats.get("current_weight") for name, stats in live.items()},
            "live_stats": live,
            "candidate_weights": candidate,
            "candidate_basis": {
                name: {
                    "trades": stats.get("trades"),
                    "rolling_win_rate": stats.get("rolling_win_rate"),
                    "profit_factor": stats.get("profit_factor"),
                    "sharpe_ratio": stats.get("sharpe_ratio"),
                }
                for name, stats in shadow_stats.items()
            },
            "note": "candidate_weights are shadow values computed from rolling metrics; the live ensemble only "
            "changes weights through its own paper-trade close path",
        }
    finally:
        if owns:
            db.close()


def train_challenger(algorithm: str = "lightgbm", model_name: str | None = None, params: dict | None = None) -> dict:
    """Queue a background challenger training job via the AI Model Lab
    runner. The result lands in the registry as a Challenger; promotion
    still requires the threshold-gated promote endpoint."""
    return ml_jobs.submit(algorithm=algorithm, model_name=model_name or f"challenger_{algorithm}", params=params or {})


def _drift_recommendations(db: Session, since: datetime) -> list[dict]:
    rows = (
        db.query(MLOpsDriftRecord)
        .filter(MLOpsDriftRecord.checked_at >= since, MLOpsDriftRecord.is_drifted.is_(True))
        .order_by(MLOpsDriftRecord.checked_at.desc())
        .limit(10)
        .all()
    )
    if not rows:
        return []
    kinds = sorted({r.drift_type for r in rows})
    return [
        {
            "type": "retrain",
            "severity": "high" if len(rows) >= 3 else "medium",
            "reason": f"{len(rows)} drift check(s) failed in the last 7 days ({', '.join(kinds)})",
            "action": "Train a challenger (POST /api/learning/train-challenger) and compare before promoting",
        }
    ]


def _accuracy_recommendations(db: Session) -> list[dict]:
    latest = db.query(LearningEvaluation).order_by(LearningEvaluation.evaluated_at.desc()).first()
    if latest is None:
        return [
            {
                "type": "evaluate",
                "severity": "low",
                "reason": "No prediction-outcome evaluation has been run yet",
                "action": "POST /api/learning/evaluate to build the accuracy baseline",
            }
        ]

    recs = []
    for timeframe, stats in (latest.by_timeframe or {}).items():
        n = stats.get("predictions") or 0
        hit = stats.get("hit_rate_pct")
        if n >= MIN_SAMPLES_FOR_JUDGMENT and hit is not None and hit < WEAK_TIMEFRAME_HIT_RATE:
            recs.append(
                {
                    "type": "weak_timeframe",
                    "severity": "medium",
                    "reason": f"{timeframe} hit rate {hit}% over {n} resolved predictions is below {WEAK_TIMEFRAME_HIT_RATE}%",
                    "action": f"Review {timeframe} strategy behavior in the Research Lab before trusting its signals",
                }
            )

    buckets = latest.confidence_reliability or []
    overconfident = [
        b
        for b in buckets
        if b.get("predictions", 0) >= MIN_SAMPLES_FOR_JUDGMENT
        and b.get("hit_rate_pct") is not None
        and b.get("avg_confidence") is not None
        and b["avg_confidence"] - b["hit_rate_pct"] > 15
    ]
    if overconfident:
        worst = max(overconfident, key=lambda b: b["avg_confidence"] - b["hit_rate_pct"])
        recs.append(
            {
                "type": "calibration",
                "severity": "medium",
                "reason": f"Confidence bucket {worst['bucket']} claims ~{worst['avg_confidence']}% but hits {worst['hit_rate_pct']}%",
                "action": "Model is overconfident in this range - consider recalibration or a higher trade threshold",
            }
        )
    return recs


def _challenger_recommendations(db: Session) -> list[dict]:
    champion = (
        db.query(MLOpsModel).filter(MLOpsModel.status == "Champion").order_by(MLOpsModel.trained_at.desc()).first()
    )
    challengers = (
        db.query(MLOpsModel).filter(MLOpsModel.status == "Challenger").order_by(MLOpsModel.trained_at.desc()).limit(5).all()
    )
    recs = []
    for ch in challengers:
        if champion is None:
            break
        ch_acc, champ_acc = ch.val_accuracy or ch.train_accuracy, champion.val_accuracy or champion.train_accuracy
        if ch_acc is not None and champ_acc is not None and ch_acc > champ_acc + 0.02:
            recs.append(
                {
                    "type": "promotion_candidate",
                    "severity": "info",
                    "reason": f"Challenger {ch.model_id} validation accuracy {ch_acc:.3f} beats champion {champ_acc:.3f}",
                    "action": f"Run its backtest + validation, then POST /api/ml/promote/{ch.model_id} if thresholds pass",
                }
            )
    return recs


def _data_quality_recommendations(db: Session, since: datetime) -> list[dict]:
    rows = (
        db.query(DataQualityReport)
        .filter(DataQualityReport.created_at >= since, DataQualityReport.quality_score < DATA_QUALITY_FLOOR)
        .order_by(DataQualityReport.created_at.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "type": "data_quality",
            "severity": "high" if r.quality_score is not None and r.quality_score < 40 else "medium",
            "reason": f"{r.symbol} {r.timeframe} scored {r.quality_score} ({r.missing_candles} missing, {r.outliers} outliers)",
            "action": f"Re-download {r.symbol} {r.timeframe} or widen its range before using it for research",
        }
        for r in rows
    ]


def recommendations(db: Session | None = None) -> dict:
    owns = db is None
    db = db or SessionLocal()
    try:
        since = datetime.now(timezone.utc) - timedelta(days=7)
        recs = (
            _drift_recommendations(db, since)
            + _accuracy_recommendations(db)
            + _challenger_recommendations(db)
            + _data_quality_recommendations(db, since)
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(recs),
            "recommendations": recs,
            "note": "Advisory only - nothing is retrained or promoted automatically",
        }
    finally:
        if owns:
            db.close()
