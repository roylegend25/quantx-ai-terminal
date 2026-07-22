import os
import tempfile

_tmp_db_fd, _tmp_db_path = tempfile.mkstemp(suffix=".db")
os.close(_tmp_db_fd)
os.environ["PAPER_DATABASE_URL"] = f"sqlite:///{_tmp_db_path}"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["DEPLOYMENT_MAINTENANCE_MODE"] = "false"
os.environ["DEPLOYMENT_MAINTENANCE_FILE"] = f"{_tmp_db_path}.maintenance"

import pytest  # noqa: E402

from app.db.session import SessionLocal, engine  # noqa: E402
from app.db.models import BinanceCredential, Portfolio, StrategyPerformance, StrategyRollingMetrics, Trade  # noqa: E402
from app.db.init_db import initialize_schema  # noqa: E402

initialize_schema(engine)
# Authority records are immutable in deployed schemas. The shared unit-test
# database deliberately removes those triggers so tests can isolate themselves
# by deleting fixtures; dedicated migration tests verify the triggers.
with engine.begin() as connection:
    for trigger in ("trading_horizon_decisions_immutable_update",
                    "trading_horizon_decisions_immutable_delete",
                    "trading_horizon_links_immutable_update",
                    "trading_horizon_links_immutable_delete"):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")


@pytest.fixture(autouse=True)
def clean_tables():
    db = SessionLocal()
    try:
        db.query(StrategyPerformance).delete()
        db.query(StrategyRollingMetrics).delete()
        db.query(Trade).delete()
        db.query(Portfolio).delete()
        # Phase 34: test_binance_credentials.py's own fixture only cleans
        # this table BEFORE its tests, not after its last one - without
        # this, a saved-credential row leaks into every later test file for
        # the rest of the session and makes binance_live_configured() (which
        # checks this table by default) silently return True in unrelated
        # tests that assume no live credential is configured.
        db.query(BinanceCredential).delete()
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture(autouse=True)
def reset_prediction_provider_failure_cooldown():
    # A test that exercises a live-provider failure sets this module-level
    # timestamp for real (see app/api/prediction.py's
    # _PROVIDER_FAILURE_COOLDOWN_SECONDS short-circuit); without resetting it
    # here, a later test running within that same 10s window that expects a
    # genuine live fetch attempt would silently get routed to cached data
    # instead - purely a test-ordering artifact, not production behavior.
    from app.api import prediction as prediction_module
    prediction_module._provider_last_failure_at = None
    yield
    prediction_module._provider_last_failure_at = None
