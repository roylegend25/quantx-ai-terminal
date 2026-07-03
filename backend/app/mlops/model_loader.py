"""Loads a persisted model artifact (joblib, written by app/ml/trainer.py's
run_training) by path, with a small in-process cache. Used by
app/mlops/evaluation.py and health checks - never by the live prediction
path, which stays on app/strategy/ensemble.py.
"""

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=16)
def load(model_path: str) -> dict:
    """Returns {"model": estimator, "feature_names": [...]}, as saved by
    app/ml/trainer.py::run_training. Raises FileNotFoundError if the
    artifact is missing (e.g. pruned from disk but not from the registry)."""
    import joblib

    path = Path(model_path)
    if not path.exists():
        raise FileNotFoundError(f"Model artifact not found: {model_path}")
    return joblib.load(path)


def is_available(model_path: str | None) -> bool:
    if not model_path:
        return False
    return Path(model_path).exists()


def clear_cache() -> None:
    load.cache_clear()
