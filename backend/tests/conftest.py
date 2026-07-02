import os
import tempfile

_tmp_db_fd, _tmp_db_path = tempfile.mkstemp(suffix=".db")
os.close(_tmp_db_fd)
os.environ["PAPER_DATABASE_URL"] = f"sqlite:///{_tmp_db_path}"
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest  # noqa: E402

from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.db.models import Portfolio, StrategyPerformance, StrategyRollingMetrics, Trade  # noqa: E402

Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def clean_tables():
    db = SessionLocal()
    try:
        db.query(StrategyPerformance).delete()
        db.query(StrategyRollingMetrics).delete()
        db.query(Trade).delete()
        db.query(Portfolio).delete()
        db.commit()
    finally:
        db.close()
    yield
