"""Version numbering and git-commit tagging for app/mlops/model_registry.py.

Every retrain gets its own "vN" - versions are derived from how many rows a
model_name already has, so they're gapless and never reused even after a
version is archived.
"""

import subprocess
from functools import lru_cache

from sqlalchemy.orm import Session

from app.db.models import MLOpsModel
from app.db.session import SessionLocal


def next_version(model_name: str, db: Session | None = None) -> str:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        count = db.query(MLOpsModel).filter(MLOpsModel.model_name == model_name).count()
        return f"v{count + 1}"
    finally:
        if owns_session:
            db.close()


@lru_cache(maxsize=1)
def current_git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None
