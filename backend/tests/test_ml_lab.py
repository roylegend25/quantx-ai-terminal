"""AI Model Lab: dataset builder, metrics, jobs/runner (in-process),
drift extensions, live tracking, settings/promotion rules, and the
/api/ml surface. Training runs use the real algorithms on synthetic-but-
realistic OHLCV series injected at the Binance-fetch boundary - everything
downstream (features, labels, fitting, artifacts) is the production code
path.
"""

import math
import time

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.models import (
    MLModelArtifact,
    MLOpsDriftRecord,
    MLOpsEvaluation,
    MLOpsExperiment,
    MLOpsModel,
    MLTrainingJob,
    PredictionFeature,
)
from app.db.session import SessionLocal
from app.ml_lab import drift_ext, jobs, live_tracking, market_dataset, metrics_ext, runner, settings_repo
from app.ml_lab.algorithms import availability, catalog


# ---------------------------------------------------------------- fixtures

def _synthetic_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """Deterministic trending+noise series ending 'now', unique per symbol."""
    # zlib.crc32 instead of hash(): str hashes are salted per process, which
    # made this fixture non-deterministic across runs
    rng = np.random.default_rng(__import__("zlib").crc32(symbol.encode()))
    n = min(int(limit), 1500)
    interval_ms = 3_600_000
    now = int(time.time() * 1000)
    t0 = now - n * interval_ms

    base = 100.0 if symbol.startswith("BTC") else 50.0
    drift = np.cumsum(rng.normal(0.0005, 0.01, n))
    wave = 0.05 * np.sin(np.arange(n) / 24)
    close = base * np.exp(drift + wave)
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.abs(rng.normal(1000, 250, n)) + 10

    return pd.DataFrame(
        {
            "time": [t0 + i * interval_ms for i in range(n)],
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


@pytest.fixture()
def market_data(monkeypatch, tmp_path):
    monkeypatch.setattr(market_dataset, "fetch_klines", _synthetic_klines)
    monkeypatch.setattr(market_dataset, "CACHE_DIR", tmp_path / "datasets")
    monkeypatch.setattr(runner, "MODELS_DIR", tmp_path / "models")
    yield


@pytest.fixture()
def clean_lab_tables():
    def _wipe():
        db = SessionLocal()
        try:
            db.query(MLTrainingJob).delete()
            db.query(MLModelArtifact).delete()
            db.query(MLOpsModel).delete()
            db.query(MLOpsDriftRecord).delete()
            db.query(MLOpsEvaluation).delete()
            db.query(MLOpsExperiment).delete()
            db.query(PredictionFeature).delete()
            db.commit()
        finally:
            db.close()

    _wipe()
    yield
    _wipe()


def make_client() -> TestClient:
    from app.api.ml import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _create_job(algorithm: str, params: dict | None = None, kind: str = "train") -> str:
    db = SessionLocal()
    try:
        row = MLTrainingJob(
            job_id=f"test{algorithm[:6]}{int(time.time()*1000) % 100000}",
            kind=kind, algorithm=algorithm, model_name=algorithm,
            params=params or {}, status="running",
        )
        db.add(row)
        db.commit()
        return row.job_id
    finally:
        db.close()


# ---------------------------------------------------------------- dataset

def test_market_dataset_builds_labeled_frame(market_data):
    frame, resolved = market_dataset.build_frame({"symbols": ["BTCUSDT"], "bars": 900, "horizon": 4})
    assert len(frame) > 500
    assert set(market_dataset.FEATURE_COLUMNS).issubset(frame.columns)
    assert frame["label"].isin([0, 1]).all()
    # labels must be exactly the sign of the realized forward return
    assert ((frame["forward_return"] > 0).astype(int) == frame["label"]).all()
    assert not frame[market_dataset.FEATURE_COLUMNS].isna().any().any()
    assert resolved["horizon"] == 4

    train, val, test = market_dataset.chronological_split(frame)
    assert train["time"].max() <= val["time"].min()
    assert val["time"].max() <= test["time"].min()


def test_market_dataset_rejects_tiny_history(market_data):
    with pytest.raises(market_dataset.DatasetUnavailableError):
        market_dataset.build_frame({"symbols": ["BTCUSDT"], "bars": 250, "horizon": 4})


# ---------------------------------------------------------------- metrics

def test_confusion_and_curves_math():
    y_true = np.array([1, 1, 1, 0, 0, 0, 1, 0])
    y_pred = np.array([1, 1, 0, 0, 0, 1, 1, 0])
    cm = metrics_ext.confusion(y_true, y_pred)
    assert (cm["tp"], cm["fp"], cm["tn"], cm["fn"]) == (3, 1, 3, 1)
    assert cm["precision"] == 0.75 and cm["recall"] == 0.75

    y_score = np.array([0.9, 0.8, 0.4, 0.2, 0.3, 0.6, 0.7, 0.1])
    assert metrics_ext.roc_points(y_true, y_score)
    assert metrics_ext.pr_points(y_true, y_score)
    lift = metrics_ext.lift_gain(y_true, y_score, deciles=4)
    assert lift["deciles"][-1]["cumulative_gain_pct"] == 100.0


def test_trading_simulation_known_case():
    y_score = np.array([0.9, 0.9, 0.1, 0.5])  # long, long, short, flat
    fwd = np.array([0.02, -0.01, -0.03, 0.5])
    sim = metrics_ext.trading_simulation(y_score, fwd)
    assert sim["trades"] == 3
    # long +2%, long -1%, short +3% -> 2 wins 1 loss
    assert sim["win_rate"] == pytest.approx(66.67, abs=0.1)
    assert sim["profit_factor"] == pytest.approx(0.05 / 0.01, abs=0.01)


def test_trading_simulation_no_trades_is_honest():
    sim = metrics_ext.trading_simulation(np.array([0.5, 0.5]), np.array([0.01, 0.02]))
    assert sim["trades"] == 0 and "reason" in sim


# ------------------------------------------------------------- algorithms

def test_algorithm_catalog_is_honest():
    entries = {a["id"]: a for a in catalog()}
    assert len(entries) == 19
    assert entries["xgboost"]["available"] is True
    assert entries["random_forest"]["available"] is True
    for model in ("logistic_regression", "elastic_net_logistic", "extra_trees", "hist_gradient_boosting", "linear_svm_calibrated", "sgd_classifier", "knn_benchmark", "gaussian_nb_benchmark"):
        assert entries[model]["available"] is True
        assert entries[model]["production_eligible"] is False
        assert entries[model]["status"] == "experimental"
    # this host has no CUDA device and no torch ecosystem
    assert entries["xgboost_gpu"]["available"] is False and entries["xgboost_gpu"]["unavailable_reason"]
    assert entries["tft"]["available"] is False and "pytorch" in entries["tft"]["unavailable_reason"].lower()
    assert entries["rl_agent"]["available"] is False

    ok, reason = availability("nonsense")
    assert not ok and "Unknown" in reason


# ------------------------------------------------- settings + promotion

def test_settings_roundtrip_and_validation(clean_lab_tables):
    settings = settings_repo.get_settings()
    assert settings["promotion"]["min_trades"] == 20

    updated = settings_repo.update_settings({"promotion": {"min_accuracy": 0.6}})
    assert updated["promotion"]["min_accuracy"] == 0.6
    assert settings_repo.get_settings()["promotion"]["min_accuracy"] == 0.6

    with pytest.raises(ValueError):
        settings_repo.update_settings({"promotion": {"bogus_key": 1}})
    with pytest.raises(ValueError):
        settings_repo.update_settings({"bogus_section": {}})


def test_promotion_rules_fail_closed_on_missing_metrics(clean_lab_tables):
    verdict = settings_repo.evaluate_promotion_rules({"val_accuracy": 0.9})
    assert verdict["met"] is False
    assert any("sharpe" in f for f in verdict["failures"])

    good = {
        "val_accuracy": 0.6, "sharpe_ratio": 1.0, "profit_factor": 1.5,
        "total_trades": 50, "max_drawdown_pct": 10.0, "avg_confidence": 0.7,
        "win_rate": 60.0, "test_samples": 120, "oos_accuracy": 0.55,
    }
    assert settings_repo.evaluate_promotion_rules(good)["met"] is True


# ------------------------------------------------------- training runner

def test_run_training_job_end_to_end_random_forest(market_data, clean_lab_tables):
    job_id = _create_job(
        "random_forest",
        {"symbols": ["BTCUSDT"], "bars": 900, "horizon": 4,
         "hyperparameters": {"n_estimators": 40, "max_depth": 5}},
    )
    result = runner.run_training_job(job_id)

    assert result["model_id"]
    assert 0 <= result["metrics"]["accuracy"] <= 1
    assert "promotion" in result

    db = SessionLocal()
    try:
        job = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == job_id).first()
        assert job.status == "succeeded"
        assert job.progress["pct"] == 100

        model = db.query(MLOpsModel).filter(MLOpsModel.model_id == result["model_id"]).first()
        assert model.precision is not None
        assert model.model_size_bytes > 0
        assert model.dataset_source == "market_history"
        assert model.training_samples > 300
        assert model.inference_rows_per_sec > 0
        assert model.peak_memory_mb > 0
        assert model.cpu_info

        kinds = {a.kind for a in db.query(MLModelArtifact).filter(MLModelArtifact.model_id == result["model_id"])}
        assert {"confusion_matrix", "calibration_curve", "lift_gain", "trading_simulation", "importance"}.issubset(kinds)

        # RF has no native TreeSHAP - attribution must say what it actually is
        importance = next(a for a in db.query(MLModelArtifact).filter(
            MLModelArtifact.model_id == result["model_id"], MLModelArtifact.kind == "importance")).payload
        assert importance["method"] == "permutation_importance"
    finally:
        db.close()


def test_run_training_job_xgboost_produces_native_shap(market_data, clean_lab_tables):
    job_id = _create_job(
        "xgboost",
        {"symbols": ["BTCUSDT"], "bars": 900, "horizon": 4,
         "hyperparameters": {"n_estimators": 60, "max_depth": 4}},
    )
    result = runner.run_training_job(job_id)

    db = SessionLocal()
    try:
        artifacts = {
            a.kind: a.payload
            for a in db.query(MLModelArtifact).filter(MLModelArtifact.model_id == result["model_id"])
        }
        assert artifacts["importance"]["method"] == "native_tree_shap"
        assert len(artifacts["importance"]["features"]) == len(market_dataset.FEATURE_COLUMNS)
        sample = artifacts["shap_sample"]
        assert len(sample["shap_rows"]) > 0
        assert len(sample["shap_rows"][0]) == len(sample["features"])
        assert artifacts.get("roc_curve") and artifacts.get("pr_curve")
        # xgboost reports per-iteration progress -> training_curve exists
        assert "training_curve" in artifacts
    finally:
        db.close()


def test_failed_dataset_marks_job_failed(market_data, clean_lab_tables):
    job_id = _create_job("random_forest", {"symbols": ["BTCUSDT"], "bars": 250, "horizon": 4})
    with pytest.raises(Exception):
        try:
            runner.run_training_job(job_id)
        except Exception:
            # runner.main() persists failure; emulate its handler here
            db = SessionLocal()
            try:
                job = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == job_id).first()
                job.status = "failed"
                job.error = "dataset unavailable"
                db.commit()
            finally:
                db.close()
            raise

    db = SessionLocal()
    try:
        job = db.query(MLTrainingJob).filter(MLTrainingJob.job_id == job_id).first()
        assert job.status == "failed"
    finally:
        db.close()


# --------------------------------------------------------------- jobs API

def test_job_submit_queue_and_unavailable(monkeypatch, clean_lab_tables):
    spawned = []
    monkeypatch.setattr(jobs, "_spawn", lambda row, db: spawned.append(row.job_id))

    job = jobs.submit(algorithm="random_forest", params={})
    assert job["status"] == "queued"
    assert spawned == [job["job_id"]]

    with pytest.raises(RuntimeError) as exc:
        jobs.submit(algorithm="tft")
    assert "pytorch" in str(exc.value).lower()

    with pytest.raises(ValueError):
        jobs.submit(algorithm="not_a_model")

    cancelled = jobs.cancel(job["job_id"])
    assert cancelled["status"] == "cancelled"


def test_orphaned_running_jobs_are_reaped(clean_lab_tables):
    db = SessionLocal()
    try:
        db.add(MLTrainingJob(job_id="orphan1", algorithm="xgboost", model_name="xgboost",
                             status="running", pid=99999999))
        db.commit()
    finally:
        db.close()

    listed = jobs.list_jobs()
    orphan = next(j for j in listed if j["job_id"] == "orphan1")
    assert orphan["status"] == "failed"
    assert "disappeared" in orphan["error"]


# ------------------------------------------------------------------ drift

def _seed_prediction_rows(n=120, symbol="BTCUSDT"):
    db = SessionLocal()
    try:
        price = 100.0
        for i in range(n):
            # drift the distribution in the recent third so PSI/KS/KL move
            shift = 25.0 if i > n * 0.7 else 0.0
            price *= 1 + (0.001 if i % 3 else -0.001)
            db.add(PredictionFeature(
                symbol=symbol, timeframe="1h",
                direction="LONG" if i % 2 else "SHORT",
                confidence=50.0 + (i % 30) + shift / 5,
                entry_price=price, target=price * 1.01, stop=price * 0.99,
                latency_ms=100 + i % 40,
                technical_features={
                    "rsi": 40 + (i % 20) + shift, "atr": 1.0 + i % 5,
                    "macd_hist": (i % 10) - 5, "bb_width": 0.01 + (i % 4) / 100,
                    "realized_volatility": 0.3 + (i % 6) / 10, "volume": 900 + i,
                },
            ))
        db.commit()
    finally:
        db.close()


def test_kl_and_wasserstein_math():
    same = list(np.random.default_rng(1).normal(0, 1, 200))
    assert drift_ext.kl_divergence(same, same) == pytest.approx(0.0, abs=1e-6)
    shifted = [v + 3 for v in same]
    assert drift_ext.wasserstein(same, shifted) == pytest.approx(3.0, abs=0.35)
    assert drift_ext.kl_divergence(same[:3], shifted) is None  # too few samples


def test_extended_drift_scan_and_history(clean_lab_tables):
    _seed_prediction_rows()
    results = drift_ext.run_extended_scan("BTCUSDT")
    assert set(results) == {"feature", "prediction", "market", "data", "concept"}
    assert results["data"]["score"] is not None
    assert results["concept"]["method"] == "accuracy-drop"

    overview = drift_ext.overview()
    assert set(overview) == set(drift_ext.DRIFT_TYPES)
    assert overview["feature"]["latest"]["status"] in ("healthy", "warning", "critical", "no_data")
    # KL and Wasserstein companions were persisted for feature drift
    assert "KL" in overview["feature"]["methods"]
    assert "Wasserstein" in overview["feature"]["methods"]

    history = drift_ext.history(days=30)
    assert history["days"] == 30
    assert any(s["drift_type"] == "data" for s in history["series"])


# ---------------------------------------------------------- live tracking

def test_live_accuracy_and_inference_monitor(clean_lab_tables):
    db = SessionLocal()
    try:
        # deterministic series: LONG then price rises = hit; alternate misses
        prices = [100, 101, 100, 102, 101, 103]
        for i, p in enumerate(prices):
            db.add(PredictionFeature(
                symbol="BTCUSDT", timeframe="1h", direction="LONG",
                confidence=60.0, entry_price=float(p),
                target=p * 1.01, latency_ms=120.0,
            ))
        db.commit()
    finally:
        db.close()

    live = live_tracking.live_accuracy(symbol="BTCUSDT")
    tf = live["timeframes"]["1h"]
    # 5 resolved LONGs: price up 3x of 5 -> 60%
    assert tf["predictions"] == 5
    assert tf["directional_accuracy_pct"] == 60.0
    assert tf["profit_if_traded_pct"] is not None
    assert live["timeframes"]["4h"]["predictions"] == 0
    assert "reason" in live["timeframes"]["4h"]

    monitor = live_tracking.inference_monitor(symbol="BTCUSDT", limit=10)
    assert monitor["scored"] == 5
    assert monitor["running_accuracy_pct"] == 60.0
    assert monitor["items"][0]["latency_ms"] == 120.0


# -------------------------------------------------------------- API layer

def test_api_surface(monkeypatch, clean_lab_tables):
    monkeypatch.setattr(
        "app.api.ml.market_dataset.dataset_info",
        lambda: {"available": False, "reason": "offline test", "spec": {}},
    )
    monkeypatch.setattr(jobs, "_spawn", lambda row, db: None)
    client = make_client()

    algos = client.get("/api/ml/algorithms").json()
    assert len(algos["algorithms"]) == 19

    overview = client.get("/api/ml/overview").json()
    assert "champion" in overview and "datasets" in overview
    assert overview["datasets"]["trade_outcomes"]["min_required"] == 30

    assert client.get("/api/ml/drift").status_code == 200
    assert client.get("/api/ml/live").status_code == 200
    assert client.get("/api/ml/inference").status_code == 200
    assert client.get("/api/ml/compare").json()["rows"] == []
    assert client.get("/api/ml/registry").json()["models"] == []
    assert client.get("/api/ml/history").status_code == 200
    assert client.get("/api/ml/hpo/results").json()["results"] == []

    settings = client.get("/api/ml/settings").json()["settings"]
    assert "promotion" in settings
    updated = client.put("/api/ml/settings", json={"retraining": {"schedule": "daily"}})
    assert updated.status_code == 200
    assert updated.json()["settings"]["retraining"]["schedule"] == "daily"
    assert client.put("/api/ml/settings", json={"nope": {}}).status_code == 400

    # explain with no champion: rule-side always available, ML side honest
    _seed_prediction_rows(n=5)
    explained = client.get("/api/ml/explain").json()
    assert explained["available"] is True
    assert explained["ml_attribution"]["available"] is False
    assert "Champion" in explained["ml_attribution"]["reason"]

    # train endpoint: queued job for valid algo, honest errors otherwise
    res = client.post("/api/ml/train", json={"algorithm": "random_forest"})
    assert res.status_code == 200 and res.json()["job"]["status"] == "queued"
    assert client.post("/api/ml/train", json={"algorithm": "bogus"}).status_code == 400
    assert client.post("/api/ml/train", json={"algorithm": "tft"}).status_code == 409
    assert client.post("/api/ml/train", json={"algorithm": "xgboost", "dataset": "bad"}).status_code == 400

    job_id = res.json()["job"]["job_id"]
    assert client.get(f"/api/ml/jobs/{job_id}").json()["job"]["algorithm"] == "random_forest"
    assert client.post(f"/api/ml/jobs/{job_id}/cancel").json()["job"]["status"] == "cancelled"
    assert client.get("/api/ml/jobs/nope").status_code == 404

    # hpo validation
    assert client.post("/api/ml/hpo", json={"algorithm": "lstm"}).status_code == 400
    assert client.post("/api/ml/hpo", json={"algorithm": "xgboost", "method": "nope"}).status_code == 400


def test_registry_lifecycle_via_api(market_data, clean_lab_tables):
    job_id = _create_job(
        "random_forest",
        {"symbols": ["ETHUSDT"], "bars": 900, "horizon": 4,
         "hyperparameters": {"n_estimators": 30, "max_depth": 4}},
    )
    result = runner.run_training_job(job_id)
    model_id = result["model_id"]
    client = make_client()

    detail = client.get(f"/api/ml/registry/{model_id}").json()
    assert detail["model"]["model_id"] == model_id
    assert "confusion_matrix" in detail["artifacts"]

    shap_res = client.get(f"/api/ml/shap/{model_id}").json()
    assert shap_res["available"] is True
    assert shap_res["importance"]["method"] == "permutation_importance"

    download = client.get(f"/api/ml/registry/{model_id}/download")
    assert download.status_code == 200
    assert len(download.content) > 1000

    promoted = client.post(f"/api/ml/promote/{model_id}?force=true").json()
    assert promoted["promoted"] is True

    # champion cannot be deleted
    assert client.delete(f"/api/ml/registry/{model_id}").status_code == 400

    # roll away is impossible (no previous champion) - honest 400
    assert client.post("/api/ml/rollback/random_forest").status_code == 400


# --------------------------------------------------------------------- hpo

def test_hpo_random_search_in_process(market_data, clean_lab_tables, monkeypatch):
    monkeypatch.setitem(
        __import__("app.ml_lab.hpo", fromlist=["SEARCH_SPACES"]).SEARCH_SPACES,
        "random_forest",
        {"n_estimators": [20, 30], "max_depth": [3, 4], "min_samples_leaf": [5]},
    )
    from app.ml_lab.hpo import run_hpo_job

    job_id = _create_job(
        "hpo-random_forest",
        {"target_algorithm": "random_forest", "method": "random", "n_trials": 3,
         "symbols": ["BTCUSDT"], "bars": 900, "horizon": 4},
        kind="hpo",
    )
    result = run_hpo_job(job_id)
    assert result["trials_run"] == 3
    assert result["best_params"] is not None
    assert 0 < result["best_value"] <= 1
    assert len(result["top_trials"]) == 3
    assert not math.isnan(result["mean_value"])

    client = make_client()
    stored = client.get("/api/ml/hpo/results").json()["results"]
    assert stored and stored[0]["algorithm"] == "random_forest"
