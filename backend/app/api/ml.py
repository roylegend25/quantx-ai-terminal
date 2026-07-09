"""AI Model Lab API.

The original three endpoints (dataset/info, /models, /performance) are
kept byte-compatible for existing consumers; everything below them is the
AI Model Lab surface: async training jobs, the model registry with
artifacts/download/delete, extended drift, live accuracy + inference
monitoring, per-prediction explainability, HPO, and lab settings.
"""

from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.models import MLModelArtifact
from app.db.session import get_db
from app.ml.feature_store import store as feature_store
from app.ml.model_registry import registry as legacy_model_registry
from app.ml_lab import (
    champion_gate,
    drift_ext,
    explain as explain_module,
    hpo as hpo_module,
    jobs,
    live_tracking,
    market_dataset,
    notifications,
    preflight,
    settings_repo,
)
from app.ml_lab.algorithms import catalog as algorithm_catalog
from app.mlops import model_manager, rollback as rollback_module
from app.mlops.evaluation import history as evaluation_history
from app.mlops.model_registry import STATUS_ARCHIVED, STATUS_CHAMPION, registry as mlops_registry
from app.mlops.retrainer import should_retrain
from app.mlops.scheduler import SCHEDULE_INTERVALS, run_scheduled_training

router = APIRouter(prefix="/api/ml", tags=["ml"])

# challengers are always reported in this order for GET /api/ml/performance,
# trained or not, so the comparison table is stable
CHALLENGER_MODELS = [
    ("XGBoost", "xgboost"),
    ("LightGBM", "lightgbm"),
    ("CatBoost", "catboost"),
]

COMPARISON_FIELDS = [
    "accuracy",
    "precision",
    "recall",
    "f1",
    "auc",
    "log_loss",
    "training_samples",
    "validation_samples",
    "test_samples",
    "status",
    "version",
    "trained_at",
]


@router.get("/dataset/info")
async def dataset_info(db: Session = Depends(get_db)):
    return feature_store.get_dataset_info(db=db)


@router.get("/models")
async def models(db: Session = Depends(get_db)):
    return {
        "champion": legacy_model_registry.get_champion(db=db),
        "challengers": legacy_model_registry.get_challengers(db=db),
    }


@router.get("/performance")
async def performance(db: Session = Depends(get_db)):
    champion = legacy_model_registry.get_champion(db=db)
    challengers_by_name = {row["model_name"]: row for row in legacy_model_registry.get_challengers(db=db)}

    def _row(row: dict | None) -> dict:
        return {field: row.get(field) for field in COMPARISON_FIELDS} if row else {field: None for field in COMPARISON_FIELDS}

    comparison = [
        {
            "model": "Adaptive Ensemble",
            "model_name": "adaptive_ensemble",
            "trained": champion is not None,
            **_row(champion),
        }
    ]

    for label, model_name in CHALLENGER_MODELS:
        row = challengers_by_name.get(model_name)
        comparison.append(
            {
                "model": label,
                "model_name": model_name,
                "trained": row is not None,
                **_row(row),
            }
        )

    return {"comparison": comparison}


# --------------------------------------------------------------------------
# AI Model Lab
# --------------------------------------------------------------------------


@router.get("/algorithms")
async def algorithms():
    return {"algorithms": algorithm_catalog(), "hpo_supported": hpo_module.supported_algorithms()}


@router.get("/overview")
async def overview(db: Session = Depends(get_db)):
    champion = mlops_registry.get_champion(db=db)
    champion_artifacts = {}
    if champion:
        rows = db.query(MLModelArtifact).filter(MLModelArtifact.model_id == champion["model_id"]).all()
        champion_artifacts = {r.kind: True for r in rows}

    running = jobs.list_jobs(status="running", db=db)
    queued = jobs.list_jobs(status="queued", db=db)

    market_info = market_dataset.dataset_info()
    trade_info = feature_store.get_dataset_info(db=db)

    return {
        "champion": champion,
        "champion_artifacts": list(champion_artifacts),
        "challengers": mlops_registry.get_challengers(db=db),
        "jobs": {"running": running, "queued": queued},
        "datasets": {
            "market_history": market_info,
            "trade_outcomes": {
                **trade_info,
                "labeled": trade_info.get("wins", 0) + trade_info.get("losses", 0),
                "min_required": 30,
            },
        },
        "retrain_triggers": should_retrain(db=db),
    }


@router.post("/train")
async def train(
    algorithm: str = Body(..., embed=True),
    dataset: str = Body("market_history", embed=True),
    symbols: list[str] | None = Body(None, embed=True),
    timeframe: str | None = Body(None, embed=True),
    bars: int | None = Body(None, embed=True),
    horizon: int | None = Body(None, embed=True),
    hyperparameters: dict | None = Body(None, embed=True),
    db: Session = Depends(get_db),
):
    if dataset not in ("market_history", "trade_outcomes"):
        raise HTTPException(status_code=400, detail="dataset must be 'market_history' or 'trade_outcomes'")
    try:
        job = jobs.submit(
            algorithm=algorithm,
            params={
                "dataset": dataset,
                "symbols": symbols,
                "timeframe": timeframe,
                "bars": bars,
                "horizon": horizon,
                "hyperparameters": hyperparameters,
            },
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"job": job}


@router.get("/jobs")
async def list_jobs(status: str | None = None, limit: int = Query(default=30, ge=1, le=200), db: Session = Depends(get_db)):
    return {"jobs": jobs.list_jobs(status=status, limit=limit, db=db)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, db: Session = Depends(get_db)):
    job = jobs.get(job_id, db=db)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return {"job": job}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, db: Session = Depends(get_db)):
    job = jobs.cancel(job_id, db=db)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return {"job": job}


@router.get("/registry")
async def registry_list(status: str | None = None, db: Session = Depends(get_db)):
    return {"models": mlops_registry.list_all(status=status, db=db)}


@router.get("/registry/{model_id}")
async def registry_detail(model_id: str, db: Session = Depends(get_db)):
    model = mlops_registry.get_by_id(model_id, db=db)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    artifacts = db.query(MLModelArtifact).filter(MLModelArtifact.model_id == model["model_id"]).all()
    return {
        "model": model,
        "artifacts": {a.kind: a.payload for a in artifacts},
        "evaluations": evaluation_history(model_id=model["model_id"], db=db),
    }


@router.get("/registry/{model_id}/download")
async def registry_download(model_id: str, db: Session = Depends(get_db)):
    model = mlops_registry.get_by_id(model_id, db=db)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    path = model.get("model_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="This version has no model file on disk (registered by the pre-lab pipeline)")
    filename = f"{model['model_name']}_{model['version']}.joblib"
    return FileResponse(path, filename=filename, media_type="application/octet-stream")


@router.delete("/registry/{model_id}")
async def registry_delete(model_id: str, db: Session = Depends(get_db)):
    """Archives the registry row (rows are never hard-deleted, matching the
    rest of the lifecycle platform) and removes the model file from disk.
    The Champion cannot be deleted - roll back first."""
    model = mlops_registry.get_by_id(model_id, db=db)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    if model["status"] == STATUS_CHAMPION:
        raise HTTPException(status_code=400, detail="Cannot delete the active Champion - promote or roll back to another version first")

    removed_file = False
    path = model.get("model_path")
    if path and Path(path).exists():
        try:
            Path(path).unlink()
            removed_file = True
        except OSError:
            pass
    mlops_registry.set_status(model_id, STATUS_ARCHIVED, db=db)
    return {"archived": True, "model_id": model_id, "model_file_removed": removed_file}


@router.post("/promote/{model_id}")
async def promote(model_id: str, force: bool = False, db: Session = Depends(get_db)):
    """Applies the configured promotion rules; `force=true` promotes anyway
    (manual override) but still reports which rules failed."""
    model = mlops_registry.get_by_id(model_id, db=db)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    rules = settings_repo.evaluate_promotion_rules(model, db=db)
    if not rules["met"] and not force:
        return {"promoted": False, **rules}

    promoted = mlops_registry.promote_to_champion(model_id, db=db)
    notifications.notify(
        notifications.EVENT_CHAMPION_PROMOTED,
        title=f"Champion promoted manually: {promoted['model_name']} {promoted['version']}",
        message=(
            "Promoted by user despite failing rules: " + "; ".join(rules["failures"][:3])
            if not rules["met"]
            else "Promoted by user - every promotion rule passed"
        ),
        severity="success" if rules["met"] else "warning",
        data={"model_id": model_id, "forced": not rules["met"]},
        db=db,
    )
    return {"promoted": True, "forced": not rules["met"], "model": promoted, **rules}


def _do_rollback(model_name: str, db: Session) -> dict:
    result = rollback_module.rollback(model_name, db=db)
    restored = result.get("rolled_back_to") or {}
    demoted = result.get("rolled_back_from") or {}
    notifications.notify(
        notifications.EVENT_CHAMPION_ROLLBACK,
        title=f"Champion rolled back: {model_name}",
        message=(
            f"Restored {restored.get('model_name')} {restored.get('version')} as Champion"
            + (f", demoted {demoted.get('version')}" if demoted else " (no Champion was active)")
        ),
        severity="warning",
        data={
            "restored_model_id": restored.get("model_id"),
            "demoted_model_id": demoted.get("model_id"),
        },
        db=db,
    )
    return result


@router.post("/rollback")
async def rollback_current(model_name: str | None = Body(None, embed=True), db: Session = Depends(get_db)):
    """Rolls the current Champion back to its previous version. Without a
    model_name in the body, targets whichever model currently holds
    Champion status."""
    if not model_name:
        champion = mlops_registry.get_champion(db=db)
        if champion is None:
            raise HTTPException(status_code=400, detail="No Champion is active, so there is nothing to roll back")
        model_name = champion["model_name"]
    try:
        return _do_rollback(model_name, db)
    except rollback_module.NoPreviousChampionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/rollback/{model_name}")
async def rollback(model_name: str, db: Session = Depends(get_db)):
    try:
        return _do_rollback(model_name, db)
    except rollback_module.NoPreviousChampionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/compare")
async def compare(db: Session = Depends(get_db)):
    """Champion + every challenger, all lab metrics, for the side-by-side
    table. Rows keep every field null-safe so untrained slots render
    honestly."""
    champion = mlops_registry.get_champion(db=db)
    challengers = mlops_registry.get_challengers(db=db)
    rows = ([{"role": "champion", **champion}] if champion else []) + [
        {"role": "challenger", **c} for c in challengers
    ]
    for row in rows:
        row["promotion_check"] = settings_repo.evaluate_promotion_rules(row, db=db)
    return {"rows": rows}


@router.get("/drift")
async def drift(db: Session = Depends(get_db)):
    return {"drift": drift_ext.overview(db=db)}


@router.get("/drift/history")
async def drift_history(days: int = Query(default=30, ge=1, le=365), db: Session = Depends(get_db)):
    return drift_ext.history(days=days, db=db)


@router.post("/drift/scan")
async def drift_scan(symbol: str = Body("BTCUSDT", embed=True), db: Session = Depends(get_db)):
    return {"scan": drift_ext.run_extended_scan(symbol, db=db)}


@router.get("/live")
async def live(symbol: str | None = None, db: Session = Depends(get_db)):
    return live_tracking.live_accuracy(symbol=symbol, db=db)


@router.get("/inference")
async def inference(symbol: str | None = None, limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    return live_tracking.inference_monitor(symbol=symbol, limit=limit, db=db)


@router.get("/history")
async def performance_history(days: int = Query(default=30, ge=1, le=365), symbol: str | None = None, db: Session = Depends(get_db)):
    """Time-series inputs for the performance-history charts: daily realized
    accuracy, every registered version's metrics, evaluation snapshots, and
    drift history - all real recorded series."""
    versions = mlops_registry.list_all(db=db)
    version_series = [
        {
            "trained_at": v["trained_at"],
            "model_name": v["model_name"],
            "version": v["version"],
            "status": v["status"],
            "accuracy": v.get("val_accuracy") or v.get("train_accuracy"),
            "win_rate": v.get("win_rate"),
            "sharpe_ratio": v.get("sharpe_ratio"),
            "profit_factor": v.get("profit_factor"),
            "precision": v.get("precision"),
            "recall": v.get("recall"),
            "max_drawdown_pct": v.get("max_drawdown_pct"),
            "avg_confidence": v.get("avg_confidence"),
        }
        for v in versions
    ]
    return {
        "days": days,
        "live_accuracy": live_tracking.accuracy_history(days=days, symbol=symbol, db=db),
        "versions": version_series,
        "evaluations": evaluation_history(limit=200, db=db),
        "drift": drift_ext.history(days=days, db=db),
    }


@router.get("/shap/{model_id}")
async def shap(model_id: str, db: Session = Depends(get_db)):
    model = mlops_registry.get_by_id(model_id, db=db)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    artifacts = {
        a.kind: a.payload
        for a in db.query(MLModelArtifact).filter(MLModelArtifact.model_id == model["model_id"]).all()
    }
    importance = artifacts.get("importance")
    if importance is None:
        return {
            "model": {"model_name": model["model_name"], "version": model["version"]},
            "available": False,
            "reason": "No attribution artifact stored for this version (trained before the AI Lab, or attribution failed)",
        }
    return {
        "model": {"model_name": model["model_name"], "version": model["version"]},
        "available": True,
        "importance": importance,
        "sample": artifacts.get("shap_sample"),
    }


@router.get("/explain")
async def explain_latest(symbol: str | None = None, db: Session = Depends(get_db)):
    return explain_module.explain_prediction(symbol=symbol, db=db)


@router.get("/explain/{prediction_id}")
async def explain_by_id(prediction_id: int, db: Session = Depends(get_db)):
    return explain_module.explain_prediction(prediction_id=prediction_id, db=db)


@router.post("/hpo")
async def start_hpo(
    algorithm: str = Body(..., embed=True),
    method: str = Body("random", embed=True),
    n_trials: int = Body(20, embed=True),
    symbols: list[str] | None = Body(None, embed=True),
    timeframe: str | None = Body(None, embed=True),
    db: Session = Depends(get_db),
):
    if algorithm not in hpo_module.supported_algorithms():
        raise HTTPException(status_code=400, detail=f"HPO supports {hpo_module.supported_algorithms()} on this host")
    if method not in hpo_module.METHODS:
        raise HTTPException(status_code=400, detail=f"method must be one of {hpo_module.METHODS}")
    if method == "bayesian":
        ok, reason = hpo_module.bayesian_available()
        if not ok:
            raise HTTPException(status_code=409, detail=f"Bayesian optimization unavailable: {reason}")
    try:
        job = jobs.submit(
            algorithm=algorithm,
            kind="hpo",
            model_name=f"hpo-{algorithm}",
            params={"target_algorithm": algorithm, "method": method, "n_trials": n_trials, "symbols": symbols, "timeframe": timeframe},
            db=db,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"job": job}


@router.get("/hpo/results")
async def hpo_results(limit: int = Query(default=10, ge=1, le=50), db: Session = Depends(get_db)):
    rows = (
        db.query(MLModelArtifact)
        .filter(MLModelArtifact.kind == "hpo_trials")
        .order_by(MLModelArtifact.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "results": [
            {"job_id": r.model_id.removeprefix("hpo:"), "created_at": r.created_at.isoformat() if r.created_at else None, **(r.payload or {})}
            for r in rows
        ]
    }


# ------------------------------------------------------- MLOps automation


@router.get("/schedule")
async def get_schedule(db: Session = Depends(get_db)):
    from app.mlops.scheduler import RUNNING, _scheduled_due

    retraining = settings_repo.get_settings(db=db)["retraining"]
    schedule = retraining.get("schedule", "manual")
    interval = SCHEDULE_INTERVALS.get(schedule)
    return {
        "schedule": retraining,
        "choices": settings_repo.SCHEDULE_CHOICES,
        "scheduler_running": RUNNING,
        "interval_hours": interval.total_seconds() / 3600 if interval else None,
        "due_now": _scheduled_due(retraining),
    }


@router.put("/schedule")
async def put_schedule(patch: dict = Body(...), db: Session = Depends(get_db)):
    """Accepts the retraining keys directly ({"schedule": "daily", ...}) or
    the full {"retraining": {...}} shape - both end up in the same settings
    section the scheduler reads on its next cycle."""
    values = patch.get("retraining") if isinstance(patch.get("retraining"), dict) else patch
    try:
        updated = settings_repo.update_settings({"retraining": values}, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"schedule": updated["retraining"], "choices": settings_repo.SCHEDULE_CHOICES}


@router.post("/train-now")
def train_now(algorithms: list[str] | None = Body(None, embed=True), db: Session = Depends(get_db)):
    """Manual trigger for the full automatic pipeline: preflight health
    gate, then one async training job per configured (or requested)
    algorithm. Sync handler on purpose - preflight builds the dataset, and
    FastAPI runs `def` endpoints in the threadpool."""
    result = run_scheduled_training(reason="manual", algorithms=algorithms, db=db)
    if not result["started"]:
        failures = "; ".join(result["preflight"]["failures"][:4])
        raise HTTPException(status_code=409, detail=f"Preflight health gate failed - training skipped: {failures}")
    return result


@router.get("/preflight")
def get_preflight(db: Session = Depends(get_db)):
    """Current pre-training health gate status (also a sync/threadpool
    handler: it builds the dataset to verify feature generation)."""
    return preflight.run_preflight(db=db)


@router.get("/champion")
async def champion(db: Session = Depends(get_db)):
    row = mlops_registry.get_champion(db=db)
    gate = champion_gate.status(db=db)
    history = (
        [
            {k: v.get(k) for k in ("model_id", "version", "status", "trained_at", "promoted_at", "val_accuracy", "win_rate")}
            for v in mlops_registry.list_versions(row["model_name"], db=db)
            if v.get("promoted_at")
        ]
        if row
        else []
    )
    return {"champion": row, "inference_gate": gate, "promotion_history": history}


@router.get("/compare/{model_id}")
async def compare_model(model_id: str, db: Session = Depends(get_db)):
    """One challenger vs the current Champion: per-metric deltas plus the
    promotion-rule evaluation, i.e. the full promotion decision input."""
    try:
        result = model_manager.compare(model_id, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    result["promotion_check"] = settings_repo.evaluate_promotion_rules(result["challenger"], db=db)
    artifact = (
        db.query(MLModelArtifact)
        .filter(MLModelArtifact.model_id == result["challenger"]["model_id"], MLModelArtifact.kind == "promotion_decision")
        .order_by(MLModelArtifact.created_at.desc())
        .first()
    )
    result["promotion_decision"] = artifact.payload if artifact else None
    return result


@router.get("/notifications")
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    unread_only: bool = False,
    db: Session = Depends(get_db),
):
    return notifications.list_notifications(limit=limit, unread_only=unread_only, db=db)


@router.post("/notifications/read-all")
async def notifications_read_all(db: Session = Depends(get_db)):
    return {"marked_read": notifications.mark_all_read(db=db)}


@router.post("/notifications/{notification_id}/read")
async def notification_read(notification_id: int, db: Session = Depends(get_db)):
    row = notifications.mark_read(notification_id, db=db)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Notification {notification_id} not found")
    return {"notification": row}


@router.get("/settings")
async def get_lab_settings(db: Session = Depends(get_db)):
    return {"settings": settings_repo.get_settings(db=db)}


@router.put("/settings")
async def put_lab_settings(patch: dict = Body(...), db: Session = Depends(get_db)):
    try:
        return {"settings": settings_repo.update_settings(patch, db=db)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/retrain")
async def retrain_now(db: Session = Depends(get_db)):
    """Evaluates the drift/accuracy triggers and, when due (or when called
    explicitly like this), queues async training jobs for the configured
    algorithms. This is the manual face of the automatic drift-triggered
    pipeline - same jobs, same rules."""
    lab = settings_repo.get_settings(db=db)["retraining"]
    triggers = should_retrain(db=db)
    submitted = []
    errors = []
    for algorithm in lab.get("drift_algorithms", ["xgboost"]):
        try:
            submitted.append(jobs.submit(algorithm=algorithm, params={"dataset": "market_history"}, db=db))
        except (ValueError, RuntimeError) as exc:
            errors.append({"algorithm": algorithm, "error": str(exc)})
    return {"triggers": triggers, "jobs": submitted, "errors": errors}


# needs the raw model row for a JSON field check - registered last so the
# path doesn't shadow anything above
@router.get("/registry/{model_id}/artifacts/{kind}")
async def registry_artifact(model_id: str, kind: str, db: Session = Depends(get_db)):
    row = (
        db.query(MLModelArtifact)
        .filter(MLModelArtifact.model_id == model_id, MLModelArtifact.kind == kind)
        .order_by(MLModelArtifact.created_at.desc())
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No '{kind}' artifact for model '{model_id}'")
    return {"model_id": model_id, "kind": kind, "payload": row.payload}
