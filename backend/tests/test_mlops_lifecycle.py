import uuid

from app.mlops import drift_detector, retrainer
from app.mlops.experiment import tracker as experiment_tracker
from app.mlops.model_manager import maybe_promote, thresholds_met
from app.mlops.model_registry import STATUS_CHALLENGER, STATUS_CHAMPION, registry
from app.mlops.rollback import NoPreviousChampionError, rollback


def _unique_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _register_passing_challenger(model_name: str) -> dict:
    return registry.register(
        model_name=model_name,
        algorithm="synthetic",
        status=STATUS_CHALLENGER,
        val_accuracy=0.9,
        win_rate=65.0,
        sharpe_ratio=1.2,
        profit_factor=1.8,
        max_drawdown_pct=5.0,
        latency_ms=50.0,
    )


def test_promotion_requires_every_threshold():
    model_name = _unique_name("threshold_model")
    weak = registry.register(
        model_name=model_name,
        algorithm="synthetic",
        status=STATUS_CHALLENGER,
        val_accuracy=0.9,
        win_rate=65.0,
        sharpe_ratio=1.2,
        profit_factor=0.5,  # below CHAMPION_MIN_PROFIT_FACTOR (1.0)
        max_drawdown_pct=5.0,
        latency_ms=50.0,
    )

    result = thresholds_met(weak["model_id"])
    assert result["met"] is False
    assert any("profit_factor" in reason for reason in result["reasons"])

    promo = maybe_promote(weak["model_id"])
    assert promo["promoted"] is False
    assert registry.get_champion(model_name=model_name) is None

    strong = _register_passing_challenger(model_name)
    promo = maybe_promote(strong["model_id"])
    assert promo["promoted"] is True

    champion = registry.get_champion(model_name=model_name)
    assert champion is not None
    assert champion["model_id"] == strong["model_id"]
    assert champion["status"] == STATUS_CHAMPION


def test_rollback_restores_previous_champion():
    model_name = _unique_name("rollback_model")

    first = _register_passing_challenger(model_name)
    assert maybe_promote(first["model_id"])["promoted"] is True

    second = _register_passing_challenger(model_name)
    assert maybe_promote(second["model_id"])["promoted"] is True
    assert registry.get_champion(model_name=model_name)["model_id"] == second["model_id"]

    result = rollback(model_name)
    assert result["rolled_back_to"]["model_id"] == first["model_id"]
    assert registry.get_champion(model_name=model_name)["model_id"] == first["model_id"]

    # every version is still present - rollback never deletes a row
    versions = registry.list_versions(model_name)
    assert {v["model_id"] for v in versions} == {first["model_id"], second["model_id"]}


def test_rollback_without_prior_champion_raises():
    model_name = _unique_name("no_history_model")
    only = _register_passing_challenger(model_name)
    maybe_promote(only["model_id"])

    try:
        rollback(model_name)
        assert False, "expected NoPreviousChampionError"
    except NoPreviousChampionError:
        pass


def test_psi_and_ks_detect_a_shifted_distribution():
    import random

    random.seed(7)
    baseline = [random.gauss(0, 1) for _ in range(300)]
    same_distribution = [random.gauss(0, 1) for _ in range(150)]
    shifted_distribution = [random.gauss(5, 1) for _ in range(150)]

    stable_psi = drift_detector.population_stability_index(baseline, same_distribution)
    drifted_psi = drift_detector.population_stability_index(baseline, shifted_distribution)
    assert stable_psi is not None and drifted_psi is not None
    assert drifted_psi > stable_psi

    stable_ks = drift_detector.ks_test(baseline, same_distribution)
    drifted_ks = drift_detector.ks_test(baseline, shifted_distribution)
    assert stable_ks["statistic"] < drifted_ks["statistic"]


def test_retrain_writes_experiment_and_registry_row_even_without_enough_data():
    model_name = _unique_name("retrain_model")

    before = len(experiment_tracker.list_all(model_name=model_name))
    result = retrainer.retrain(model_name, algorithm="xgboost", reason="manual")

    # with no labeled outcomes seeded in this test's DB, InsufficientDataError
    # is expected - the important thing is that it's handled gracefully
    # (no exception escapes) and still leaves an audit trail
    assert result["status"] == "skipped"
    after = experiment_tracker.list_all(model_name=model_name)
    assert len(after) == before + 1
    assert after[0]["status"] == "skipped"
