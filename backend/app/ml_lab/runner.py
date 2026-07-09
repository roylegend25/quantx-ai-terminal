"""Training worker process: `python -m app.ml_lab.runner <job_id>`.

Owns one MLTrainingJob row end-to-end: builds the dataset, fits the model
with live progress reporting, evaluates on the chronological holdout,
persists the model file + evaluation artifacts, registers the version in
the mlops registry, and applies the configured auto-promotion rules.

Runs outside the API process on purpose: TensorFlow/xgboost fitting here
can peg both cores and the trading backend keeps serving predictions.
"""

from __future__ import annotations

import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psutil

from app.db.models import MLModelArtifact, MLTrainingJob
from app.db.session import SessionLocal
from app.ml_lab import market_dataset, metrics_ext, notifications, shap_native, walk_forward
from app.ml_lab.algorithms import ALGORITHMS, SEQUENCE_ALGORITHMS, build_estimator
from app.ml_lab.settings_repo import evaluate_promotion_rules
from app.mlops.experiment import tracker as experiment_tracker
from app.mlops.model_registry import STATUS_CHALLENGER, registry

MODELS_DIR = Path("/app/data/ml_lab/models")


def _cpu_info() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return f"{psutil.cpu_count()} vCPU"


class ProgressWriter:
    """Writes progress into the job row. Each call also samples this
    process's CPU/RSS so the UI's resource readouts are measurements, not
    estimates."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.proc = psutil.Process()
        self.peak_rss_mb = 0.0
        self.history: list[dict] = []
        self.proc.cpu_percent()  # prime the interval sampler

    def __call__(self, **fields):
        rss_mb = self.proc.memory_info().rss / 1e6
        self.peak_rss_mb = max(self.peak_rss_mb, rss_mb)
        payload = {
            **fields,
            "cpu_pct": round(self.proc.cpu_percent(), 1),
            "ram_mb": round(rss_mb, 1),
            "gpu_pct": None,  # no CUDA device on this host
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if "loss" in fields and fields.get("epoch") is not None:
            self.history.append(
                {k: fields.get(k) for k in ("epoch", "loss", "val_loss", "accuracy", "val_accuracy")}
            )
            payload["history"] = self.history[-60:]
        db = SessionLocal()
        try:
            row = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == self.job_id).first()
            if row is not None:
                row.progress = payload
                db.commit()
        finally:
            db.close()


def _load_dataset(params: dict, report: ProgressWriter):
    source = params.get("dataset", "market_history")
    if source == "trade_outcomes":
        from app.ml.dataset_builder import (
            InsufficientDataError,
            chronological_split,
            feature_columns,
            load_labeled_frame,
        )

        report(stage="dataset", pct=8, message="Loading outcome-labeled predictions from the feature store")
        try:
            frame = load_labeled_frame()
        except Exception as exc:  # pragma: no cover - session issues
            raise RuntimeError(f"Feature store unavailable: {exc}") from exc
        columns = feature_columns(frame)
        try:
            train_df, val_df, test_df = chronological_split(frame)
        except InsufficientDataError as exc:
            raise RuntimeError(str(exc)) from exc
        fwd = test_df.get("realized_return")
        forward_returns = fwd.fillna(0.0).to_numpy(dtype=float) if fwd is not None else np.zeros(len(test_df))
        spec = {"source": source, "rows": len(frame)}
        return (
            train_df[columns].fillna(0.0), train_df["label"].to_numpy(),
            val_df[columns].fillna(0.0), val_df["label"].to_numpy(),
            test_df[columns].fillna(0.0), test_df["label"].to_numpy(),
            forward_returns, columns, spec,
        )

    report(stage="dataset", pct=8, message="Building market-history dataset from Binance klines")
    frame, resolved = market_dataset.build_frame(
        {
            "symbols": params.get("symbols") or market_dataset.DEFAULT_SPEC["symbols"],
            "timeframe": params.get("timeframe") or market_dataset.DEFAULT_SPEC["timeframe"],
            "bars": params.get("bars") or market_dataset.DEFAULT_SPEC["bars"],
            "horizon": params.get("horizon") or market_dataset.DEFAULT_SPEC["horizon"],
        }
    )
    columns = market_dataset.FEATURE_COLUMNS
    train_df, val_df, test_df = market_dataset.chronological_split(frame)
    spec = {
        "source": "market_history",
        **resolved,
        "rows": len(frame),
        "range": {"first_bar": int(frame["time"].min()), "last_bar": int(frame["time"].max())},
    }
    return (
        train_df[columns], train_df["label"].to_numpy(),
        val_df[columns], val_df["label"].to_numpy(),
        test_df[columns], test_df["label"].to_numpy(),
        test_df["forward_return"].to_numpy(dtype=float), columns, spec,
    )


def _fit_tabular(algorithm, hp, X_train, y_train, X_val, y_val, report):
    estimator = build_estimator(algorithm, hp)
    cls = type(estimator).__name__
    total = int(hp.get("n_estimators", hp.get("iterations", 300)))
    started = time.time()

    def iteration_progress(i: int, loss: float | None = None):
        elapsed = max(1e-6, time.time() - started)
        rate = (i + 1) / elapsed
        report(
            stage="training", pct=15 + int(70 * (i + 1) / total),
            epoch=i + 1, total_epochs=total,
            loss=loss, val_loss=loss,
            learning_rate=hp.get("learning_rate"),
            eta_seconds=round((total - i - 1) / rate, 1),
            samples_per_sec=round(len(X_train) * rate, 1),
        )

    if cls == "XGBClassifier":
        from xgboost.callback import TrainingCallback

        class _Cb(TrainingCallback):
            def after_iteration(self, model, epoch, evals_log):
                if epoch % 20 == 0 or epoch == total - 1:
                    loss = None
                    val = evals_log.get("validation_0", {}).get("logloss")
                    if val:
                        loss = round(float(val[-1]), 5)
                    iteration_progress(epoch, loss)
                return False

        estimator.set_params(callbacks=[_Cb()])
        estimator.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
        # the local callback class can't be pickled - drop it before the
        # fitted model is joblib-dumped
        estimator.set_params(callbacks=None)
    elif cls == "LGBMClassifier":
        import lightgbm as lgb

        def _cb(env):
            if env.iteration % 20 == 0 or env.iteration == total - 1:
                loss = round(float(env.evaluation_result_list[0][2]), 5) if env.evaluation_result_list else None
                iteration_progress(env.iteration, loss)

        estimator.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=[_cb, lgb.log_evaluation(-1)])
    else:
        report(stage="training", pct=30, message=f"Fitting {cls} ({len(X_train)} samples)")
        estimator.fit(X_train, y_train)
        report(stage="training", pct=80, message=f"{cls} fit complete")
    return estimator


def _fit_sequence(algorithm, hp, X_train, y_train, X_val, y_val, X_test, y_test, columns, report):
    from app.ml_lab.keras_models import EpochProgress, build_model, make_sequences

    seq_len = int(hp.get("seq_len", 16))
    epochs = int(hp.get("epochs", 12))

    report(stage="preprocessing", pct=15, message=f"Standardizing features and windowing sequences (seq_len={seq_len})")
    mean = X_train.mean(axis=0).to_numpy(dtype=float)
    scale = X_train.std(axis=0).replace(0, 1.0).to_numpy(dtype=float)

    def norm(df):
        return (df.to_numpy(dtype=float) - mean) / scale

    Xtr, ytr = make_sequences(norm(X_train), y_train, seq_len)
    Xva, yva = make_sequences(norm(X_val), y_val, seq_len)
    Xte, yte = make_sequences(norm(X_test), y_test, seq_len)
    if len(Xtr) < 100 or len(Xte) < 20:
        raise RuntimeError(
            f"Not enough rows to window sequences (train {len(Xtr)}, test {len(Xte)}) - increase dataset bars"
        )

    model = build_model(algorithm, seq_len, len(columns), hp)
    bridge = EpochProgress(epochs, len(Xtr), report)
    model.fit(
        Xtr, ytr,
        validation_data=(Xva, yva) if len(Xva) else None,
        epochs=epochs,
        batch_size=int(hp.get("batch_size", 64)),
        verbose=0,
        callbacks=[bridge.keras_callback()],
    )
    return model, (Xte, yte), {"mean": mean.tolist(), "scale": scale.tolist(), "seq_len": seq_len}, bridge.history


def _save_artifact(db, model_id: str, kind: str, payload):
    db.add(MLModelArtifact(model_id=model_id, kind=kind, payload=payload))
    db.commit()


def run_training_job(job_id: str) -> dict:
    report = ProgressWriter(job_id)
    db = SessionLocal()
    try:
        job = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == job_id).first()
        if job is None:
            raise RuntimeError(f"Job '{job_id}' not found")
        params = job.params or {}
        algorithm = job.algorithm
        hp = {**ALGORITHMS[algorithm]["default_hyperparameters"], **(params.get("hyperparameters") or {})}

        experiment = experiment_tracker.start(
            model_name=job.model_name, algorithm=algorithm,
            hyperparameters=hp,
            dataset_version=params.get("dataset", "market_history"),
            feature_version="ml-lab-v1", db=db,
        )

        X_train, y_train, X_val, y_val, X_test, y_test, forward_returns, columns, spec = _load_dataset(params, report)

        started = time.time()
        scaler_meta = None
        training_curve = None

        if algorithm in SEQUENCE_ALGORITHMS:
            model, (Xte_seq, yte_seq), scaler_meta, training_curve = _fit_sequence(
                algorithm, hp, X_train, y_train, X_val, y_val, X_test, y_test, columns, report
            )
            report(stage="evaluating", pct=88, message="Scoring holdout slice")
            y_score = model.predict(Xte_seq, verbose=0).reshape(-1)
            y_eval = yte_seq
            # sequence windowing trims the first seq_len-1 rows of the slice
            forward_eval = forward_returns[scaler_meta["seq_len"] - 1:]
            bench_X = Xte_seq
        else:
            model = _fit_tabular(algorithm, hp, X_train, y_train, X_val, y_val, report)
            report(stage="evaluating", pct=88, message="Scoring holdout slice")
            y_score = model.predict_proba(X_test)[:, 1]
            y_eval = y_test
            forward_eval = forward_returns
            bench_X = X_test
            training_curve = report.history or None

        duration = round(time.time() - started, 2)
        y_pred = (y_score >= 0.5).astype(int)

        cls_metrics = metrics_ext.classification_metrics(y_eval, y_pred, y_score)
        cm = metrics_ext.confusion(y_eval, y_pred)
        roc = metrics_ext.roc_points(y_eval, y_score)
        pr = metrics_ext.pr_points(y_eval, y_score)
        calibration = metrics_ext.calibration_points(y_eval, y_score)
        lift = metrics_ext.lift_gain(y_eval, y_score)
        sim = metrics_ext.trading_simulation(y_score, forward_eval)

        # inference throughput measured on the real holdout matrix
        bench_start = time.perf_counter()
        if algorithm in SEQUENCE_ALGORITHMS:
            model.predict(bench_X, verbose=0)
        else:
            model.predict_proba(bench_X)
        bench_elapsed = max(1e-6, time.perf_counter() - bench_start)
        rows_per_sec = round(len(bench_X) / bench_elapsed, 1)
        latency_ms = round(bench_elapsed / max(1, len(bench_X)) * 1000, 4)

        report(stage="walk_forward", pct=90, message="Walk-forward validation (expanding-window out-of-sample folds)")
        wf = walk_forward.run(
            algorithm,
            hp,
            pd.concat([X_train, X_val, X_test]),
            np.concatenate([y_train, y_val, y_test]),
        )

        report(stage="attribution", pct=92, message="Computing feature attribution")
        if algorithm in SEQUENCE_ALGORITHMS:
            importance = {
                "method": "unavailable",
                "reason": "Sequence models have no per-feature tree attribution; use the tree challengers' SHAP for feature analysis",
                "features": [],
            }
        else:
            importance = shap_native.shap_summary(model, X_test, y_eval)

        report(stage="saving", pct=95, message="Persisting model + registry entry")
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        version_stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        if algorithm in SEQUENCE_ALGORITHMS:
            keras_path = MODELS_DIR / f"{algorithm}_{version_stamp}.keras"
            model.save(keras_path)
            bundle = {"kind": "keras", "keras_path": str(keras_path), "feature_names": columns, **scaler_meta}
        else:
            bundle = {"kind": "sklearn", "model": model, "feature_names": columns}
        model_path = MODELS_DIR / f"{algorithm}_{version_stamp}.joblib"
        joblib.dump(bundle, model_path)
        size_bytes = model_path.stat().st_size + (
            Path(bundle["keras_path"]).stat().st_size if bundle.get("keras_path") else 0
        )

        registered = registry.register(
            model_name=job.model_name, algorithm=algorithm, status=STATUS_CHALLENGER,
            dataset_version=json.dumps(spec.get("source")),
            feature_version="ml-lab-v1",
            train_accuracy=cls_metrics["accuracy"], val_accuracy=cls_metrics["accuracy"],
            win_rate=sim.get("win_rate"), sharpe_ratio=sim.get("sharpe_ratio"),
            profit_factor=sim.get("profit_factor"), max_drawdown_pct=sim.get("max_drawdown_pct"),
            latency_ms=latency_ms, training_duration_seconds=duration,
            parameters=hp, feature_list=columns, model_path=str(model_path),
            notes=f"AI Model Lab job {job_id}", db=db,
        )
        model_id = registered["model_id"]
        registry.update_metrics(
            model_id,
            model_size_bytes=int(size_bytes),
            training_samples=int(len(X_train)),
            test_samples=int(len(y_eval)),
            oos_accuracy=wf.get("mean_accuracy"),
            dataset_source=spec.get("source"),
            dataset_spec=spec,
            precision=cls_metrics["precision"], recall=cls_metrics["recall"],
            f1=cls_metrics["f1"], roc_auc=cls_metrics["auc"],
            avg_confidence=round(float(np.mean(np.maximum(y_score, 1 - y_score))), 4),
            avg_prediction_error=round(float(np.mean(np.abs(y_score - y_eval))), 4),
            total_trades=sim.get("trades"),
            inference_rows_per_sec=rows_per_sec,
            peak_memory_mb=round(report.peak_rss_mb, 1),
            cpu_info=_cpu_info(),
            gpu_info="none (CPU-only host)",
            db=db,
        )

        _save_artifact(db, model_id, "confusion_matrix", cm)
        if roc:
            _save_artifact(db, model_id, "roc_curve", roc)
        if pr:
            _save_artifact(db, model_id, "pr_curve", pr)
        _save_artifact(db, model_id, "calibration_curve", calibration)
        _save_artifact(db, model_id, "lift_gain", lift)
        _save_artifact(db, model_id, "trading_simulation", sim)
        _save_artifact(db, model_id, "walk_forward", wf)
        _save_artifact(db, model_id, "importance", {k: v for k, v in importance.items() if k != "sample"})
        if importance.get("sample"):
            _save_artifact(db, model_id, "shap_sample", importance["sample"])
        if training_curve:
            _save_artifact(db, model_id, "training_curve", training_curve)

        final_model = registry.get_by_id(model_id, db=db)
        rules = evaluate_promotion_rules(final_model, db=db)

        # champion comparison + drift snapshot recorded alongside the rules
        # so every promotion (or rejection) is fully explainable after the
        # fact. Drift is informational here, not a gate: this challenger
        # was just trained on post-drift data, so elevated market drift is
        # the reason it exists, not evidence against it.
        champion_before = registry.get_champion(db=db)
        try:
            from app.mlops.model_manager import compare as compare_to_champion

            champion_diff = compare_to_champion(model_id, db=db)["diff"] if champion_before else None
        except Exception:
            champion_diff = None
        try:
            from app.mlops.drift_detector import latest_by_type

            drift_snapshot = {
                k: {"is_drifted": v.get("is_drifted"), "score": v.get("score")}
                for k, v in latest_by_type(db=db).items() if v
            }
        except Exception:
            drift_snapshot = {}

        promoted = False
        if rules["auto_promote_enabled"] and rules["met"]:
            registry.promote_to_champion(model_id, db=db)
            promoted = True

        decision = {
            "promoted": promoted,
            "auto_promote_enabled": rules["auto_promote_enabled"],
            "rules_met": rules["met"],
            "failures": rules["failures"],
            "detail": rules["detail"],
            "walk_forward": wf,
            "vs_champion": champion_diff,
            "previous_champion": (
                {"model_id": champion_before["model_id"], "version": champion_before["version"],
                 "model_name": champion_before["model_name"]}
                if champion_before else None
            ),
            "drift_at_decision": drift_snapshot,
        }
        _save_artifact(db, model_id, "promotion_decision", decision)

        acc_pct = f"{cls_metrics['accuracy'] * 100:.1f}%"
        notifications.notify(
            notifications.EVENT_CHALLENGER_CREATED,
            title=f"New challenger: {job.model_name} {registered['version']}",
            message=f"{algorithm} trained on {spec.get('source')} ({len(X_train)} samples) - holdout accuracy {acc_pct}",
            severity="info",
            data={"model_id": model_id, "job_id": job_id, "metrics": cls_metrics},
            db=db,
        )
        if promoted:
            notifications.notify(
                notifications.EVENT_CHAMPION_PROMOTED,
                title=f"Champion promoted: {job.model_name} {registered['version']}",
                message=(
                    f"Cleared every promotion rule (accuracy {acc_pct}, "
                    f"walk-forward {wf.get('mean_accuracy', 'n/a')})"
                    + (f"; replaced {champion_before['model_name']} {champion_before['version']}" if champion_before else "")
                ),
                severity="success",
                data={"model_id": model_id, "decision": {k: decision[k] for k in ("rules_met", "failures", "previous_champion")}},
                db=db,
            )
        elif rules["auto_promote_enabled"]:
            notifications.notify(
                notifications.EVENT_CHALLENGER_REJECTED,
                title=f"Challenger rejected: {job.model_name} {registered['version']}",
                message="Kept current Champion - failed: " + "; ".join(rules["failures"][:4]),
                severity="warning",
                data={"model_id": model_id, "failures": rules["failures"]},
                db=db,
            )

        experiment_tracker.complete(
            experiment["experiment_id"],
            metrics={**cls_metrics, **{f"sim_{k}": v for k, v in sim.items() if k != "reason"}},
            model_id=model_id, status="success", db=db,
        )

        result = {
            "model_id": model_id,
            "version": registered["version"],
            "metrics": cls_metrics,
            "trading_simulation": sim,
            "walk_forward": wf,
            "promotion": {"promoted": promoted, **rules},
        }

        notifications.notify(
            notifications.EVENT_TRAINING_COMPLETED,
            title=f"Training completed: {job.model_name} {registered['version']}",
            message=(
                f"{algorithm} finished in {duration}s - accuracy {acc_pct}, "
                f"{'promoted to Champion' if promoted else 'registered as Challenger'}"
            ),
            severity="success",
            data={"job_id": job_id, "model_id": model_id},
            db=db,
        )

        job = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == job_id).first()
        job.status = "succeeded"
        job.result_model_id = model_id
        job.result = result
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        report(stage="done", pct=100, message=f"Registered {registered['version']} ({'promoted to Champion' if promoted else 'Challenger'})")
        return result
    finally:
        db.close()


def main():
    job_id = sys.argv[1]

    def _terminated(signum, frame):
        # jobs.cancel() already marked the row cancelled; just exit cleanly
        sys.exit(143)

    signal.signal(signal.SIGTERM, _terminated)

    db = SessionLocal()
    try:
        job = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == job_id).first()
        kind = job.kind if job else "train"
    finally:
        db.close()

    try:
        if kind == "hpo":
            from app.ml_lab.hpo import run_hpo_job

            run_hpo_job(job_id)
        else:
            run_training_job(job_id)
    except SystemExit:
        raise
    except Exception as exc:
        db = SessionLocal()
        try:
            job = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == job_id).first()
            if job is not None and job.status not in ("cancelled",):
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.finished_at = datetime.now(timezone.utc)
                db.commit()
                notifications.notify(
                    notifications.EVENT_TRAINING_FAILED,
                    title=f"Training failed: {job.model_name}",
                    message=f"{job.algorithm} job {job_id}: {type(exc).__name__}: {exc}",
                    severity="error",
                    data={"job_id": job_id, "algorithm": job.algorithm},
                    db=db,
                )
        finally:
            db.close()
        raise


if __name__ == "__main__":
    main()
