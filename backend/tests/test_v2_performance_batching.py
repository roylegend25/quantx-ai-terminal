"""Stage 1 performance audit found ~30-35 candidates per Active Drive V2
evaluate() call, each separately querying repository.performance() (up to
2 SQL round trips each) - ~40-80 round trips per request. This proves the
batched replacement (repository.performance_batch(), wired into
v2.py::evaluate()) produces IDENTICAL results to the old per-candidate
calls, using a query count that stays flat regardless of candidate count."""
from datetime import datetime, timezone

from sqlalchemy import event

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.decision_engine.repository import performance, performance_batch
from app.decision_engine.v2 import ActiveDriveV2Engine

from tests.test_active_drive_v2 import _seed_resolved_predictions, legacy


def _count_queries(fn):
    queries = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    return result, queries


def test_performance_batch_matches_per_identity_performance():
    db = SessionLocal()
    try:
        _seed_resolved_predictions(db, source_name="batch-check-a", source_version="2.1.0",
                                   symbol="BTCUSDT", timeframe="15m", regime="TRENDING", n=30, win_ratio=0.7)
        _seed_resolved_predictions(db, source_name="batch-check-b", source_version="2.1.0",
                                   symbol="BTCUSDT", timeframe="15m", regime="TRENDING", n=15, win_ratio=0.4)

        expected_a = performance(db, settings.admin_username, "batch-check-a", "2.1.0", "BTCUSDT", "15m", "TRENDING")
        expected_b = performance(db, settings.admin_username, "batch-check-b", "2.1.0", "BTCUSDT", "15m", "TRENDING")

        batched = performance_batch(db, settings.admin_username,
                                    [("batch-check-a", "2.1.0"), ("batch-check-b", "2.1.0"), ("never-seen", "9.9.9")],
                                    "BTCUSDT", "15m", "TRENDING")

        assert batched[("batch-check-a", "2.1.0")] == expected_a
        assert batched[("batch-check-b", "2.1.0")] == expected_b
        assert batched[("never-seen", "9.9.9")]["resolved"] == 0
    finally:
        db.close()


def test_v2_evaluate_query_count_stays_flat_regardless_of_candidate_count():
    """The whole point of batching: query count must not scale with
    candidate count (~30-35 candidates in production). A regression here
    means someone reintroduced a per-candidate query."""
    db = SessionLocal()
    try:
        for i in range(10):
            _seed_resolved_predictions(db, source_name=f"flat-check-{i}", source_version="2.1.0",
                                       symbol="BTCUSDT", timeframe="1h", regime="TRENDING", n=25, win_ratio=0.6)

        context = {"db": db, "symbol": "BTCUSDT", "timeframe": "1h", "legacy": legacy(), "regime": "TRENDING",
                   "data_status": "live", "risk_reward_ratio": 2.0}
        _, queries = _count_queries(lambda: ActiveDriveV2Engine().evaluate(context))

        # ~30-35 candidates today (legacy strategies + strategy_votes +
        # quant_votes + champion + 8 shadow models) would have been 40-80+
        # round trips under the old per-candidate performance() calls -
        # batching bounds this to a small, candidate-count-independent
        # ceiling (eligibility batch + performance batch x<=2 + settings/
        # history lookups).
        assert len(queries) < 20, f"evaluate() issued {len(queries)} SQL statements - looks unbounded/per-candidate again"
    finally:
        db.close()
