from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.ml.feature_store import store as feature_store
from app.ml.model_registry import registry as model_registry

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
        "champion": model_registry.get_champion(db=db),
        "challengers": model_registry.get_challengers(db=db),
    }


@router.get("/performance")
async def performance(db: Session = Depends(get_db)):
    champion = model_registry.get_champion(db=db)
    challengers_by_name = {row["model_name"]: row for row in model_registry.get_challengers(db=db)}

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
