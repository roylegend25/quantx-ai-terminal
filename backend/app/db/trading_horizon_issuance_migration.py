"""Add issuance fingerprint uniqueness for Horizon authority idempotency.

Controlled deployment only; this module is not run automatically.
"""
from sqlalchemy import inspect, text
from app.db.session import engine
from app.db.trading_horizon_migration import REVISION as BASE_REVISION

REVISION = "20260716_02_horizon_issuance_fingerprint"


def _install_postgres_immutability(connection) -> bool:
    if connection.dialect.name != "postgresql":
        return False
    connection.execute(text("""CREATE OR REPLACE FUNCTION trading_horizon_reject_mutation()
        RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'IMMUTABLE_TRADING_HORIZON_RECORD'; END; $$
        LANGUAGE plpgsql"""))
    changed = False
    for trigger, table in (
        ("trading_horizon_decisions_immutable_update", "trading_horizon_decisions"),
        ("trading_horizon_decisions_immutable_delete", "trading_horizon_decisions"),
        ("trading_horizon_links_immutable_update", "trading_horizon_timeframe_links"),
        ("trading_horizon_links_immutable_delete", "trading_horizon_timeframe_links"),
    ):
        exists = connection.execute(text("SELECT 1 FROM pg_trigger WHERE tgname=:name AND NOT tgisinternal"),
                                    {"name": trigger}).first()
        if not exists:
            operation = "UPDATE" if trigger.endswith("update") else "DELETE"
            connection.execute(text(f"CREATE TRIGGER {trigger} BEFORE {operation} ON {table} "
                                    "FOR EACH ROW EXECUTE FUNCTION trading_horizon_reject_mutation()"))
            changed = True
    return changed


def upgrade(bind=engine) -> bool:
    with bind.begin() as connection:
        timestamp_type = "TIMESTAMP WITH TIME ZONE" if connection.dialect.name == "postgresql" else "DATETIME"
        connection.execute(text(f"CREATE TABLE IF NOT EXISTS schema_migrations (revision VARCHAR PRIMARY KEY, applied_at {timestamp_type} NOT NULL)"))
        if not connection.execute(text("SELECT 1 FROM schema_migrations WHERE revision=:revision"),
                                  {"revision": BASE_REVISION}).first():
            raise RuntimeError(f"Required predecessor migration is missing: {BASE_REVISION}")
        recorded = bool(connection.execute(text("SELECT 1 FROM schema_migrations WHERE revision=:revision"),
                                           {"revision": REVISION}).first())
        inspector = inspect(connection)
        if "trading_horizon_decisions" not in inspector.get_table_names():
            raise RuntimeError("Trading Horizon base migration must be applied first")
        columns = {column["name"] for column in inspector.get_columns("trading_horizon_decisions")}
        changed = False
        if "issuance_fingerprint" not in columns:
            connection.execute(text("ALTER TABLE trading_horizon_decisions ADD COLUMN issuance_fingerprint VARCHAR"))
            changed = True
        indexes = inspector.get_indexes("trading_horizon_decisions")
        if not any(index.get("name") == "ix_trading_horizon_decisions_issuance_fingerprint"
                   and index.get("unique") for index in indexes):
            connection.execute(text("CREATE UNIQUE INDEX ix_trading_horizon_decisions_issuance_fingerprint ON trading_horizon_decisions (issuance_fingerprint)"))
            changed = True
        if not recorded:
            connection.execute(text("INSERT INTO schema_migrations (revision, applied_at) VALUES (:revision, CURRENT_TIMESTAMP)"),
                               {"revision": REVISION})
            changed = True
        changed = _install_postgres_immutability(connection) or changed
    return changed
