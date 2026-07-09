"""Rollback to the previous Champion (POST /api/models/rollback/{id}).

Never deletes a model version - a rollback just re-promotes an Archived row
and demotes the current Champion back to Archived, exactly like
model_registry.promote_to_champion does for a normal promotion.
"""

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.mlops.model_registry import STATUS_ARCHIVED, registry


class NoPreviousChampionError(RuntimeError):
    pass


def rollback(model_name: str, db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        current = registry.get_champion(model_name=model_name, db=db)

        # The crown can move between algorithms (e.g. xgboost -> ensemble_ml),
        # so the previous Champion may live in another lineage entirely: it is
        # whichever Archived row was promoted most recently, across all names.
        candidates = [
            version
            for version in registry.list_all(db=db)
            if version["status"] == STATUS_ARCHIVED
            and version["promoted_at"]
            and (current is None or version["model_id"] != current["model_id"])
        ]
        if not candidates:
            raise NoPreviousChampionError(f"No previous Champion found for '{model_name}' to roll back to")

        previous_champion = max(candidates, key=lambda v: v["promoted_at"])
        restored = registry.promote_to_champion(previous_champion["model_id"], db=db)
        return {"rolled_back_from": current, "rolled_back_to": restored}
    finally:
        if owns_session:
            db.close()
