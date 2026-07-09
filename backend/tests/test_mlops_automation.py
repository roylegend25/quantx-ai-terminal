"""MLOps automation layer: notifications (in-app + external gating),
schedule API, train-now with the preflight health gate, extended promotion
rules (beat-champion margin, walk-forward OOS), rollback with audit
notifications, walk-forward validation math, and the Champion inference
safety gate."""

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.models import MLNotification, MLOpsModel, MLTrainingJob
from app.db.session import SessionLocal
from app.ml_lab import champion_gate, notifications, preflight, settings_repo, walk_forward
from app.ml_lab import jobs as lab_jobs
from app.mlops import scheduler
from app.mlops.model_registry import registry


@pytest.fixture(autouse=True)
def clean_mlops_tables():
    def _wipe():
        db = SessionLocal()
        try:
            db.query(MLNotification).delete()
            db.query(MLTrainingJob).delete()
            db.query(MLOpsModel).delete()
            db.commit()
        finally:
            db.close()

    _wipe()
    champion_gate.clear_cache()
    yield
    _wipe()


@pytest.fixture()
def reset_lab_settings():
    """Force the settings singleton back to defaults before and after."""
    def _reset():
        db = SessionLocal()
        try:
            from app.db.models import MLLabSettings

            row = db.get(MLLabSettings, 1)
            if row is not None:
                row.data = {k: dict(v) for k, v in settings_repo.DEFAULTS.items()}
                db.commit()
        finally:
            db.close()

    _reset()
    yield
    _reset()


def make_client() -> TestClient:
    from app.api.ml import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------- notifications

def test_notify_records_and_lists():
    stored = notifications.notify(
        notifications.EVENT_TRAINING_COMPLETED,
        title="Training completed: xgboost v3",
        message="accuracy 57.5%",
        severity="success",
        data={"model_id": "abc123"},
    )
    assert stored["id"] and stored["read"] is False

    listed = notifications.list_notifications()
    assert listed["unread"] == 1
    assert listed["notifications"][0]["event"] == "training_completed"
    # no external env vars configured in tests -> in-app only
    assert listed["external_channels"] == []

    assert notifications.mark_read(stored["id"])["read"] is True
    assert notifications.list_notifications()["unread"] == 0


def test_notify_rejects_unknown_event_and_severity():
    with pytest.raises(ValueError):
        notifications.notify("made_up_event", title="x")
    with pytest.raises(ValueError):
        notifications.notify(notifications.EVENT_DRIFT_WARNING, title="x", severity="loud")


def test_warning_events_are_deduped_inside_window():
    first = notifications.notify(notifications.EVENT_DRIFT_WARNING, title="drift!")
    assert first is not None
    # identical event inside the 6h window is suppressed
    assert notifications.notify(notifications.EVENT_DRIFT_WARNING, title="drift again") is None
    # non-deduped events are never suppressed
    assert notifications.notify(notifications.EVENT_TRAINING_STARTED, title="t1") is not None
    assert notifications.notify(notifications.EVENT_TRAINING_STARTED, title="t2") is not None


def test_mark_all_read():
    notifications.notify(notifications.EVENT_TRAINING_STARTED, title="a")
    notifications.notify(notifications.EVENT_TRAINING_COMPLETED, title="b")
    assert notifications.mark_all_read() == 2
    assert notifications.list_notifications()["unread"] == 0


def test_notifications_api(reset_lab_settings):
    client = make_client()
    notifications.notify(notifications.EVENT_CHALLENGER_CREATED, title="new challenger")

    res = client.get("/api/ml/notifications").json()
    assert res["unread"] == 1
    nid = res["notifications"][0]["id"]

    assert client.post(f"/api/ml/notifications/{nid}/read").json()["notification"]["read"] is True
    assert client.post("/api/ml/notifications/99999/read").status_code == 404
    assert client.post("/api/ml/notifications/read-all").status_code == 200


# ------------------------------------------------------------- schedule

def test_schedule_get_put_roundtrip(reset_lab_settings):
    client = make_client()

    res = client.get("/api/ml/schedule").json()
    assert res["schedule"]["schedule"] == "manual"
    assert "every_6_hours" in res["choices"]
    assert res["due_now"] is False  # manual never becomes due

    updated = client.put("/api/ml/schedule", json={"schedule": "every_6_hours"})
    assert updated.status_code == 200
    assert updated.json()["schedule"]["schedule"] == "every_6_hours"

    res = client.get("/api/ml/schedule").json()
    assert res["interval_hours"] == 6
    assert res["due_now"] is True  # never ran -> due immediately

    assert client.put("/api/ml/schedule", json={"schedule": "hourly"}).status_code == 400
    assert client.put("/api/ml/schedule", json={"schedule_algorithms": ["bogus"]}).status_code == 400


def test_scheduled_due_logic(reset_lab_settings):
    from datetime import datetime, timedelta, timezone

    assert scheduler._scheduled_due({"schedule": "manual"}) is False
    assert scheduler._scheduled_due({"schedule": "every_6_hours", "last_scheduled_run": None}) is True

    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    assert scheduler._scheduled_due({"schedule": "every_6_hours", "last_scheduled_run": recent}) is False
    assert scheduler._scheduled_due({"schedule": "every_6_hours", "last_scheduled_run": stale}) is True
    assert scheduler._scheduled_due({"schedule": "daily", "last_scheduled_run": stale}) is False


# ---------------------------------------------- train-now + health gate

def _healthy_gate(params=None, db=None):
    return {"healthy": True, "checks": [], "failures": [], "checked_at": "now"}


def _sick_gate(params=None, db=None):
    return {
        "healthy": False,
        "checks": [{"name": "data_quality:BTCUSDT", "passed": False, "detail": "score 12"}],
        "failures": ["data_quality:BTCUSDT: score 12"],
        "checked_at": "now",
    }


def test_train_now_queues_available_algorithms(monkeypatch, reset_lab_settings):
    monkeypatch.setattr(preflight, "run_preflight", _healthy_gate)
    monkeypatch.setattr(lab_jobs, "_spawn", lambda row, db: None)
    client = make_client()

    res = client.post("/api/ml/train-now", json={"algorithms": ["random_forest", "xgboost_gpu"]})
    assert res.status_code == 200
    body = res.json()
    assert body["started"] is True
    assert [j["algorithm"] for j in body["jobs"]] == ["random_forest"]
    # GPU model skipped with the honest reason, not silently dropped
    assert body["skipped"][0]["algorithm"] == "xgboost_gpu"
    assert "CUDA" in body["skipped"][0]["reason"] or "GPU" in body["skipped"][0]["reason"]

    # the run stamped last_scheduled_run
    assert settings_repo.get_settings()["retraining"]["last_scheduled_run"] is not None


def test_train_now_skips_and_notifies_when_unhealthy(monkeypatch, reset_lab_settings):
    monkeypatch.setattr(preflight, "run_preflight", _sick_gate)
    client = make_client()

    res = client.post("/api/ml/train-now", json={})
    assert res.status_code == 409
    assert "Preflight" in res.json()["detail"]

    events = {n["event"] for n in notifications.list_notifications()["notifications"]}
    assert "training_skipped" in events
    assert "data_quality_warning" in events

    db = SessionLocal()
    try:
        assert db.query(MLTrainingJob).count() == 0
    finally:
        db.close()


def test_preflight_reports_dataset_failure(monkeypatch, reset_lab_settings):
    from app.ml_lab import market_dataset

    monkeypatch.setattr(market_dataset, "dataset_info", lambda spec=None: {"available": False, "reason": "feed down"})
    # pin host checks green so the assertion tests the dataset gate, not
    # whatever RAM the CI box happens to have free right now
    monkeypatch.setattr(
        preflight, "_system_checks",
        lambda gate, db: [{"name": "database", "passed": True, "detail": "ok"}],
    )
    gate = preflight.run_preflight()
    byname = {c["name"]: c for c in gate["checks"]}
    assert gate["healthy"] is False
    assert byname["historical_data"]["passed"] is False
    assert "feed down" in byname["historical_data"]["detail"]
    assert byname["feature_generation"]["passed"] is False


# ------------------------------------------------ promotion rules (new)

GOOD_METRICS = dict(
    val_accuracy=0.62, win_rate=58.0, sharpe_ratio=1.1, profit_factor=1.4,
    max_drawdown_pct=8.0,
)


def _register(status="Challenger", **overrides):
    fields = {**GOOD_METRICS, **overrides}
    row = registry.register(model_name="xgboost", algorithm="xgboost", status=status, **fields)
    registry.update_metrics(row["model_id"], avg_confidence=0.7, total_trades=40, test_samples=120, oos_accuracy=0.56)
    return registry.get_by_id(row["model_id"])


def test_promotion_requires_beating_champion_by_margin(reset_lab_settings):
    champ = _register()
    registry.promote_to_champion(champ["model_id"])

    # margin defaults to 1%: champion has 0.62, so a challenger needs >= 0.6262
    barely_worse = _register(val_accuracy=0.625)
    verdict = settings_repo.evaluate_promotion_rules(barely_worse)
    assert verdict["met"] is False
    assert any("beats_champion" in f for f in verdict["failures"])

    clearly_better = _register(val_accuracy=0.64)
    assert settings_repo.evaluate_promotion_rules(clearly_better)["met"] is True

    # the champion evaluated against itself has nothing to beat
    champ_now = registry.get_champion()
    assert settings_repo.evaluate_promotion_rules(champ_now)["detail"]["beats_champion"]["passed"] is True


def test_promotion_fails_without_walk_forward_or_samples(reset_lab_settings):
    model = _register()
    incomplete = {**model, "oos_accuracy": None}
    verdict = settings_repo.evaluate_promotion_rules(incomplete)
    assert verdict["met"] is False
    assert any("oos_accuracy" in f for f in verdict["failures"])

    small = {**model, "test_samples": 10}
    verdict = settings_repo.evaluate_promotion_rules(small)
    assert any("test_samples" in f for f in verdict["failures"])


# --------------------------------------------------------------- rollback

def test_rollback_endpoint_restores_previous_champion(reset_lab_settings):
    client = make_client()

    v1 = _register()
    registry.promote_to_champion(v1["model_id"])
    v2 = _register(val_accuracy=0.66)
    registry.promote_to_champion(v2["model_id"])  # v1 -> Archived with promoted_at

    res = client.post("/api/ml/rollback", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["rolled_back_to"]["model_id"] == v1["model_id"]
    assert body["rolled_back_from"]["model_id"] == v2["model_id"]

    assert registry.get_champion()["model_id"] == v1["model_id"]
    # full audit trail: the demoted version is archived, not deleted
    assert registry.get_by_id(v2["model_id"])["status"] == "Archived"

    events = [n["event"] for n in notifications.list_notifications()["notifications"]]
    assert "champion_rollback" in events

    # rolling back again restores v2 (the most recently promoted archived
    # version) - versions flip, nothing is ever deleted
    res2 = client.post("/api/ml/rollback", json={})
    assert res2.status_code == 200
    assert res2.json()["rolled_back_to"]["model_id"] == v2["model_id"]


def test_rollback_without_champion_is_a_clean_400(reset_lab_settings):
    client = make_client()
    res = client.post("/api/ml/rollback", json={})
    assert res.status_code == 400
    assert "No Champion" in res.json()["detail"]


# ------------------------------------------------------------ walk-forward

def test_walk_forward_expanding_window_math():
    rng = np.random.default_rng(7)
    n = 600
    X = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n)})
    # learnable rule with noise
    y = ((X["a"] + 0.3 * rng.normal(size=n)) > 0).astype(int).to_numpy()

    result = walk_forward.run("random_forest", {"n_estimators": 30, "max_depth": 4}, X, y)
    assert result["available"] is True
    assert result["n_folds"] >= 3
    assert 0.6 < result["mean_accuracy"] <= 1.0
    # every fold trained strictly on the past
    for prev, cur in zip(result["folds"], result["folds"][1:]):
        assert cur["train_rows"] > prev["train_rows"]


def test_walk_forward_skips_sequence_models_and_tiny_data():
    X = pd.DataFrame({"a": np.arange(100.0)})
    y = np.zeros(100, dtype=int)
    assert walk_forward.run("lstm", {}, X, y)["available"] is False
    assert walk_forward.run("random_forest", {}, X, y)["available"] is False


# --------------------------------------------------------- champion gate

FEATURES = {
    "rsi": 65.0, "volume": 1200.0, "volume_sma20": 1000.0, "regime": "bullish_trend",
}


def _make_champion_with_file(tmp_path):
    import joblib
    from sklearn.ensemble import RandomForestClassifier

    rng = np.random.default_rng(3)
    X = rng.normal(size=(200, 3))
    y = (X[:, 0] > 0).astype(int)
    model = RandomForestClassifier(n_estimators=20, random_state=0).fit(X, y)

    path = tmp_path / "champ.joblib"
    joblib.dump({"kind": "sklearn", "model": model, "feature_names": ["rsi", "volume_ratio", "regime_bullish_trend"]}, path)

    row = registry.register(model_name="xgboost", algorithm="xgboost", status="Challenger", model_path=str(path), val_accuracy=0.6)
    registry.promote_to_champion(row["model_id"])
    return row


def test_champion_gate_is_off_by_default(tmp_path, reset_lab_settings):
    _make_champion_with_file(tmp_path)
    result = champion_gate.maybe_predict(FEATURES)
    assert result["used"] is False
    assert "disabled" in result["reason"]

    status = champion_gate.status()
    assert status["enabled"] is False and status["champion_exists"] is True and status["healthy"] is True
    assert status["active"] is False


def test_champion_gate_predicts_when_enabled_and_healthy(tmp_path, reset_lab_settings):
    _make_champion_with_file(tmp_path)
    settings_repo.update_settings({"inference": {"champion_enabled": True}})

    result = champion_gate.maybe_predict(FEATURES)
    assert result["used"] is True
    assert 0.0 <= result["p_up"] <= 1.0
    assert result["direction"] in ("LONG", "SHORT")
    assert champion_gate.status()["active"] is True


def test_champion_gate_falls_back_on_bad_data_or_missing_features(tmp_path, reset_lab_settings):
    _make_champion_with_file(tmp_path)
    settings_repo.update_settings({"inference": {"champion_enabled": True}})

    bad_quality = champion_gate.maybe_predict(FEATURES, data_quality={"reliable": False, "reason": "gaps"})
    assert bad_quality["used"] is False and "gaps" in bad_quality["reason"]

    missing = champion_gate.maybe_predict({"volume": 1.0})
    assert missing["used"] is False and "missing" in missing["reason"]


def test_champion_gate_falls_back_without_champion(reset_lab_settings):
    settings_repo.update_settings({"inference": {"champion_enabled": True}})
    result = champion_gate.maybe_predict(FEATURES)
    assert result["used"] is False
    assert "No ML Champion" in result["reason"]
