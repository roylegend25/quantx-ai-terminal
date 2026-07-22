"""The shared engine must actually run in WAL mode with a non-zero busy
timeout - this is what lets the scheduler's writes (decisions, trades)
proceed concurrently with the dashboard's many reads instead of both
serializing behind SQLite's default rollback-journal locking, and what
keeps a genuine writer-vs-writer collision retrying briefly instead of
immediately raising "database is locked"."""
from sqlalchemy import text

from app.db.session import engine, IS_SQLITE


def test_sqlite_connections_run_in_wal_mode_with_busy_timeout():
    if not IS_SQLITE:
        return
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert connection.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL


def test_a_second_connection_from_the_pool_also_gets_the_pragmas():
    if not IS_SQLITE:
        return
    with engine.connect() as first, engine.connect() as second:
        for connection in (first, second):
            assert connection.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
            assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 5000
