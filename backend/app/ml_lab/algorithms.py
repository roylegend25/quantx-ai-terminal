"""Algorithm catalog for one-click training.

Every entry either trains for real on this host or reports exactly why it
can't (`available=False` + reason) - nothing pretends to train. Estimator
imports are deferred into the factory functions so the API process never
pays the import cost (TensorFlow in particular) - only the worker
subprocess does.

Availability is decided from the actual runtime environment:
  - tree/sklearn models: libraries verified installed at image build
  - Keras models: TensorFlow present AND enough free RAM to fit on this
    2-vCPU box without OOM-killing the trading backend
  - xgboost_gpu: requires a CUDA device, detected at call time
  - TFT / RL: their ecosystems (pytorch-forecasting / stable-baselines3)
    are not installed in this image - listed, disabled, with the reason
"""

from __future__ import annotations

import importlib.util
import shutil

import psutil

KERAS_MIN_AVAILABLE_RAM_MB = 900

SEQUENCE_ALGORITHMS = {"lstm", "cnn_lstm", "transformer"}


def _lib_installed(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _cuda_available() -> bool:
    return shutil.which("nvidia-smi") is not None


def _keras_availability() -> tuple[bool, str | None]:
    if not _lib_installed("tensorflow"):
        return False, "TensorFlow is not installed in this backend image"
    available_mb = psutil.virtual_memory().available / 1e6
    if available_mb < KERAS_MIN_AVAILABLE_RAM_MB:
        return False, (
            f"Only {available_mb:.0f}MB RAM free on this host; Keras training needs "
            f">= {KERAS_MIN_AVAILABLE_RAM_MB}MB to run without destabilizing the live backend"
        )
    return True, None


ALGORITHMS: dict[str, dict] = {
    "logistic_regression": {"label":"Logistic Regression","family":"linear","supports_shap":False,"production_eligible":False,"default_hyperparameters":{"c":1.0}},
    "elastic_net_logistic": {"label":"Elastic-Net Logistic Regression","family":"linear","supports_shap":False,"production_eligible":False,"default_hyperparameters":{"c":1.0,"l1_ratio":0.5}},
    "extra_trees": {"label":"Extra Trees","family":"bagging","supports_shap":False,"production_eligible":False,"default_hyperparameters":{"n_estimators":300,"max_depth":8,"min_samples_leaf":5}},
    "hist_gradient_boosting": {"label":"HistGradientBoosting","family":"gradient_boosting","supports_shap":False,"production_eligible":False,"default_hyperparameters":{"max_iter":200,"learning_rate":0.05,"max_leaf_nodes":31}},
    "linear_svm_calibrated": {"label":"Calibrated Linear SVM","family":"linear","supports_shap":False,"production_eligible":False,"default_hyperparameters":{"c":1.0}},
    "sgd_classifier": {"label":"SGD Classifier","family":"linear","supports_shap":False,"production_eligible":False,"default_hyperparameters":{"alpha":0.0001}},
    "knn_benchmark": {"label":"KNN (Shadow Benchmark)","family":"benchmark","supports_shap":False,"production_eligible":False,"default_hyperparameters":{"n_neighbors":11}},
    "gaussian_nb_benchmark": {"label":"Gaussian NB (Shadow Benchmark)","family":"benchmark","supports_shap":False,"production_eligible":False,"default_hyperparameters":{}},
    "xgboost": {
        "label": "XGBoost",
        "family": "gradient_boosting",
        "supports_shap": True,
        "default_hyperparameters": {"n_estimators": 300, "max_depth": 5, "learning_rate": 0.05, "subsample": 0.9},
    },
    "lightgbm": {
        "label": "LightGBM",
        "family": "gradient_boosting",
        "supports_shap": True,
        "default_hyperparameters": {"n_estimators": 400, "num_leaves": 31, "learning_rate": 0.05, "subsample": 0.9},
    },
    "catboost": {
        "label": "CatBoost",
        "family": "gradient_boosting",
        "supports_shap": True,
        "default_hyperparameters": {"iterations": 300, "depth": 5, "learning_rate": 0.05},
    },
    "random_forest": {
        "label": "Random Forest",
        "family": "bagging",
        "supports_shap": False,
        "default_hyperparameters": {"n_estimators": 300, "max_depth": 8, "min_samples_leaf": 5},
    },
    "ensemble_ml": {
        "label": "Ensemble ML",
        "family": "ensemble",
        "supports_shap": False,
        "default_hyperparameters": {"n_estimators": 200, "max_depth": 5, "learning_rate": 0.05},
        "description": "Soft-voting ensemble of XGBoost + LightGBM + Random Forest",
    },
    "xgboost_gpu": {
        "label": "XGBoost GPU",
        "family": "gradient_boosting",
        "supports_shap": True,
        "default_hyperparameters": {"n_estimators": 500, "max_depth": 6, "learning_rate": 0.05},
    },
    "lstm": {
        "label": "LSTM",
        "family": "deep_learning",
        "supports_shap": False,
        "default_hyperparameters": {"seq_len": 16, "units": 32, "epochs": 12, "batch_size": 64, "learning_rate": 0.001},
    },
    "cnn_lstm": {
        "label": "CNN-LSTM",
        "family": "deep_learning",
        "supports_shap": False,
        "default_hyperparameters": {"seq_len": 16, "filters": 16, "units": 32, "epochs": 12, "batch_size": 64, "learning_rate": 0.001},
    },
    "transformer": {
        "label": "Transformer",
        "family": "deep_learning",
        "supports_shap": False,
        "default_hyperparameters": {"seq_len": 16, "d_model": 32, "heads": 2, "epochs": 12, "batch_size": 64, "learning_rate": 0.001},
    },
    "tft": {
        "label": "Temporal Fusion Transformer",
        "family": "deep_learning",
        "supports_shap": False,
        "default_hyperparameters": {},
    },
    "rl_agent": {
        "label": "Reinforcement Learning Agent",
        "family": "reinforcement_learning",
        "supports_shap": False,
        "default_hyperparameters": {},
    },
}


def availability(algorithm: str) -> tuple[bool, str | None]:
    if algorithm not in ALGORITHMS:
        return False, f"Unknown algorithm '{algorithm}'"
    if algorithm in ("xgboost", "random_forest", "ensemble_ml", "logistic_regression", "elastic_net_logistic",
                     "extra_trees", "hist_gradient_boosting", "linear_svm_calibrated", "sgd_classifier",
                     "knn_benchmark", "gaussian_nb_benchmark"):
        return True, None
    if algorithm == "lightgbm":
        return (True, None) if _lib_installed("lightgbm") else (False, "lightgbm is not installed")
    if algorithm == "catboost":
        return (True, None) if _lib_installed("catboost") else (False, "catboost is not installed")
    if algorithm == "xgboost_gpu":
        if not _cuda_available():
            return False, "No NVIDIA GPU / CUDA driver detected on this host - CPU XGBoost is available instead"
        return True, None
    if algorithm in SEQUENCE_ALGORITHMS:
        return _keras_availability()
    if algorithm == "tft":
        return False, "Requires pytorch-forecasting (PyTorch), which is not installed in this image"
    if algorithm == "rl_agent":
        return False, "Requires stable-baselines3 + gymnasium, which are not installed in this image"
    return False, f"No availability rule for '{algorithm}'"


def catalog() -> list[dict]:
    out = []
    for algo_id, meta in ALGORITHMS.items():
        ok, reason = availability(algo_id)
        out.append(
            {
                "id": algo_id,
                "label": meta["label"],
                "family": meta["family"],
                "available": ok,
                "unavailable_reason": reason,
                "supports_shap": meta["supports_shap"],
                "default_hyperparameters": meta["default_hyperparameters"],
                "description": meta.get("description"),
                "production_eligible": meta.get("production_eligible", True),
                "status": "experimental" if not meta.get("production_eligible", True) else "available",
            }
        )
    return out


def build_estimator(algorithm: str, hyperparameters: dict):
    """Instantiates the sklearn-API estimator for tabular algorithms.
    Sequence (Keras) models are built in keras_models.py instead."""
    hp = hyperparameters or {}

    if algorithm in ("logistic_regression", "elastic_net_logistic"):
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        params={"C":float(hp.get("c",1.0)),"max_iter":1000,"random_state":42}
        if algorithm=="elastic_net_logistic": params.update(penalty="elasticnet",solver="saga",l1_ratio=float(hp.get("l1_ratio",.5)))
        return make_pipeline(StandardScaler(),LogisticRegression(**params))
    if algorithm == "extra_trees":
        from sklearn.ensemble import ExtraTreesClassifier
        return ExtraTreesClassifier(n_estimators=int(hp.get("n_estimators",300)),max_depth=int(hp.get("max_depth",8)),min_samples_leaf=int(hp.get("min_samples_leaf",5)),n_jobs=2,random_state=42)
    if algorithm == "hist_gradient_boosting":
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(max_iter=int(hp.get("max_iter",200)),learning_rate=float(hp.get("learning_rate",.05)),max_leaf_nodes=int(hp.get("max_leaf_nodes",31)),random_state=42)
    if algorithm == "linear_svm_calibrated":
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.svm import LinearSVC
        return make_pipeline(StandardScaler(),CalibratedClassifierCV(LinearSVC(C=float(hp.get("c",1.0)),random_state=42),cv=3))
    if algorithm == "sgd_classifier":
        from sklearn.linear_model import SGDClassifier
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        return make_pipeline(StandardScaler(),SGDClassifier(loss="log_loss",alpha=float(hp.get("alpha",.0001)),random_state=42))
    if algorithm == "knn_benchmark":
        from sklearn.neighbors import KNeighborsClassifier
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        return make_pipeline(StandardScaler(),KNeighborsClassifier(n_neighbors=int(hp.get("n_neighbors",11))))
    if algorithm == "gaussian_nb_benchmark":
        from sklearn.naive_bayes import GaussianNB
        return GaussianNB()

    if algorithm in ("xgboost", "xgboost_gpu"):
        from xgboost import XGBClassifier

        extra = {"device": "cuda", "tree_method": "hist"} if algorithm == "xgboost_gpu" else {"n_jobs": 2}
        return XGBClassifier(
            n_estimators=int(hp.get("n_estimators", 300)),
            max_depth=int(hp.get("max_depth", 5)),
            learning_rate=float(hp.get("learning_rate", 0.05)),
            subsample=float(hp.get("subsample", 0.9)),
            eval_metric="logloss",
            **extra,
        )
    if algorithm == "lightgbm":
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=int(hp.get("n_estimators", 400)),
            num_leaves=int(hp.get("num_leaves", 31)),
            learning_rate=float(hp.get("learning_rate", 0.05)),
            subsample=float(hp.get("subsample", 0.9)),
            n_jobs=2,
            verbosity=-1,
        )
    if algorithm == "catboost":
        from catboost import CatBoostClassifier

        return CatBoostClassifier(
            iterations=int(hp.get("iterations", 300)),
            depth=int(hp.get("depth", 5)),
            learning_rate=float(hp.get("learning_rate", 0.05)),
            verbose=False,
            allow_writing_files=False,
            thread_count=2,
        )
    if algorithm == "random_forest":
        from sklearn.ensemble import RandomForestClassifier

        return RandomForestClassifier(
            n_estimators=int(hp.get("n_estimators", 300)),
            max_depth=int(hp.get("max_depth", 8)),
            min_samples_leaf=int(hp.get("min_samples_leaf", 5)),
            n_jobs=2,
            random_state=42,
        )
    if algorithm == "ensemble_ml":
        from sklearn.ensemble import RandomForestClassifier, VotingClassifier
        from xgboost import XGBClassifier

        members = [
            ("xgb", XGBClassifier(
                n_estimators=int(hp.get("n_estimators", 200)),
                max_depth=int(hp.get("max_depth", 5)),
                learning_rate=float(hp.get("learning_rate", 0.05)),
                eval_metric="logloss", n_jobs=2,
            )),
            ("rf", RandomForestClassifier(
                n_estimators=int(hp.get("n_estimators", 200)),
                max_depth=int(hp.get("max_depth", 8)),
                n_jobs=2, random_state=42,
            )),
        ]
        try:
            from lightgbm import LGBMClassifier

            members.insert(1, ("lgbm", LGBMClassifier(
                n_estimators=int(hp.get("n_estimators", 200)),
                learning_rate=float(hp.get("learning_rate", 0.05)),
                n_jobs=2, verbosity=-1,
            )))
        except ImportError:
            pass
        return VotingClassifier(estimators=members, voting="soft", n_jobs=1)

    raise ValueError(f"build_estimator does not handle '{algorithm}'")
