"""Hyperparameter optimization as background jobs (kind='hpo').

Three real search methods over the market-history dataset:
  - grid: exhaustive sklearn ParameterGrid
  - random: sklearn ParameterSampler
  - bayesian: Optuna TPE (listed as unavailable with the reason if the
    optuna package is missing from the image)

Each trial is a genuine fit+validate cycle (train split / validation
split, chronological). Results - every trial, the best parameters, and
parameter importances where Optuna can compute them - are stored on the
job row and as an `hpo_trials` artifact attached to no model (model_id =
"hpo:<job_id>") so they survive job-list pruning.
"""

from __future__ import annotations

import importlib.util
import time
from datetime import datetime, timezone

import numpy as np

from app.db.models import MLModelArtifact, MLTrainingJob
from app.db.session import SessionLocal
from app.ml_lab import market_dataset
from app.ml_lab.algorithms import ALGORITHMS, SEQUENCE_ALGORITHMS, build_estimator

SEARCH_SPACES = {
    "xgboost": {
        "n_estimators": [100, 200, 300, 500],
        "max_depth": [3, 4, 5, 6, 8],
        "learning_rate": [0.01, 0.03, 0.05, 0.1],
        "subsample": [0.7, 0.85, 1.0],
    },
    "lightgbm": {
        "n_estimators": [100, 200, 400, 600],
        "num_leaves": [15, 31, 63],
        "learning_rate": [0.01, 0.03, 0.05, 0.1],
        "subsample": [0.7, 0.85, 1.0],
    },
    "catboost": {
        "iterations": [100, 200, 300, 500],
        "depth": [3, 4, 5, 6],
        "learning_rate": [0.01, 0.03, 0.05, 0.1],
    },
    "random_forest": {
        "n_estimators": [100, 200, 300, 500],
        "max_depth": [4, 6, 8, 12],
        "min_samples_leaf": [2, 5, 10],
    },
}

METHODS = ("grid", "random", "bayesian")


def bayesian_available() -> tuple[bool, str | None]:
    if importlib.util.find_spec("optuna") is None:
        return False, "optuna is not installed in this backend image"
    return True, None


def supported_algorithms() -> list[str]:
    return list(SEARCH_SPACES)


def _validation_accuracy(algorithm: str, params: dict, data) -> float:
    X_train, y_train, X_val, y_val = data
    estimator = build_estimator(algorithm, params)
    estimator.fit(X_train, y_train)
    return float((estimator.predict(X_val) == y_val).mean())


def _progress(job_id: str, payload: dict):
    db = SessionLocal()
    try:
        row = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == job_id).first()
        if row is not None:
            row.progress = {**payload, "updated_at": datetime.now(timezone.utc).isoformat()}
            db.commit()
    finally:
        db.close()


def run_hpo_job(job_id: str):
    db = SessionLocal()
    try:
        job = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == job_id).first()
        params = job.params or {}
    finally:
        db.close()

    algorithm = params.get("target_algorithm") or "xgboost"
    method = params.get("method", "random")
    n_trials = min(int(params.get("n_trials", 20)), 60)

    if algorithm in SEQUENCE_ALGORITHMS:
        raise RuntimeError("HPO for sequence models is not offered on this CPU-only host - each trial would take minutes and starve live trading")
    if algorithm not in SEARCH_SPACES:
        raise RuntimeError(f"No search space defined for '{algorithm}'. Supported: {supported_algorithms()}")
    if method not in METHODS:
        raise RuntimeError(f"Unknown method '{method}'. Supported: {METHODS}")
    if method == "bayesian":
        ok, reason = bayesian_available()
        if not ok:
            raise RuntimeError(f"Bayesian optimization unavailable: {reason}")

    _progress(job_id, {"stage": "dataset", "pct": 5, "message": "Building dataset for trials"})
    frame, resolved = market_dataset.build_frame(
        {
            "symbols": params.get("symbols") or market_dataset.DEFAULT_SPEC["symbols"],
            "timeframe": params.get("timeframe") or market_dataset.DEFAULT_SPEC["timeframe"],
            "bars": params.get("bars") or market_dataset.DEFAULT_SPEC["bars"],
            "horizon": params.get("horizon") or market_dataset.DEFAULT_SPEC["horizon"],
        }
    )
    columns = market_dataset.FEATURE_COLUMNS
    train_df, val_df, _ = market_dataset.chronological_split(frame)
    data = (train_df[columns], train_df["label"].to_numpy(), val_df[columns], val_df["label"].to_numpy())

    space = SEARCH_SPACES[algorithm]
    trials: list[dict] = []
    best = {"value": -1.0, "params": None}
    started = time.time()
    param_importance = None

    def record(i: int, total: int, trial_params: dict, value: float):
        trials.append({"trial": i + 1, "params": trial_params, "value": round(value, 4)})
        if value > best["value"]:
            best["value"] = value
            best["params"] = trial_params
        elapsed = max(1e-6, time.time() - started)
        _progress(job_id, {
            "stage": "optimizing", "pct": 10 + int(85 * (i + 1) / total),
            "trial": i + 1, "total_trials": total,
            "best_value": round(best["value"], 4), "best_params": best["params"],
            "eta_seconds": round((total - i - 1) * elapsed / (i + 1), 1),
        })

    if method == "grid":
        from sklearn.model_selection import ParameterGrid

        grid = list(ParameterGrid(space))[:n_trials]
        for i, trial_params in enumerate(grid):
            record(i, len(grid), trial_params, _validation_accuracy(algorithm, trial_params, data))
    elif method == "random":
        from sklearn.model_selection import ParameterSampler

        sampled = list(ParameterSampler(space, n_iter=n_trials, random_state=42))
        for i, trial_params in enumerate(sampled):
            record(i, len(sampled), trial_params, _validation_accuracy(algorithm, trial_params, data))
    else:
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)

        def objective(trial: "optuna.Trial") -> float:
            trial_params = {name: trial.suggest_categorical(name, values) for name, values in space.items()}
            value = _validation_accuracy(algorithm, trial_params, data)
            record(trial.number, n_trials, trial_params, value)
            return value

        study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=n_trials)
        try:
            importances = optuna.importance.get_param_importances(study)
            param_importance = {k: round(float(v), 4) for k, v in importances.items()}
        except Exception:
            param_importance = None

    top_trials = sorted(trials, key=lambda t: -t["value"])[:20]
    result = {
        "algorithm": algorithm,
        "method": method,
        "dataset": resolved,
        "trials_run": len(trials),
        "best_value": round(best["value"], 4),
        "best_params": best["params"],
        "top_trials": top_trials,
        "param_importance": param_importance,
        "history": [{"trial": t["trial"], "value": t["value"]} for t in trials],
        "mean_value": round(float(np.mean([t["value"] for t in trials])), 4) if trials else None,
    }

    db = SessionLocal()
    try:
        db.add(MLModelArtifact(model_id=f"hpo:{job_id}", kind="hpo_trials", payload=result))
        job = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == job_id).first()
        job.status = "succeeded"
        job.result = result
        job.finished_at = datetime.now(timezone.utc)
        job.progress = {"stage": "done", "pct": 100, "message": f"Best {algorithm} validation accuracy {best['value']:.4f} over {len(trials)} trials"}
        db.commit()
    finally:
        db.close()
    return result
