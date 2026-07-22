from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os

DATABASE_URL = os.getenv("PAPER_DATABASE_URL", "sqlite:////app/data/paper.db")
IS_SQLITE = DATABASE_URL.startswith("sqlite")

connect_args = {"check_same_thread": False} if IS_SQLITE else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

if IS_SQLITE:
    # WAL lets the scheduler's writes (decisions, trades) proceed
    # concurrently with the dashboard's many reads instead of serializing
    # behind SQLite's default rollback-journal locking - the single biggest
    # lever available for the read/write contention behind the concurrency
    # spikes, since one write no longer blocks every concurrent reader.
    # busy_timeout makes a session that DOES hit a writer-vs-writer
    # collision retry internally for up to 5s instead of raising "database
    # is locked" immediately. synchronous=NORMAL is the standard, safe
    # pairing with WAL (an OS crash can still lose the last WAL-only commit,
    # but an application crash cannot - unlike full fsync-per-commit,
    # already-committed data on disk is never corrupted).
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
