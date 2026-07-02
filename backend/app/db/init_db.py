from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from app.db.session import engine, Base, SessionLocal
from app.db.models import Portfolio
from app.strategy.performance_repository import repository as performance_repository

def _migrate_trade_columns():
    """Add columns introduced after the trades table already existed on disk."""
    inspector = inspect(engine)
    if "trades" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("trades")}
    with engine.begin() as conn:
        if "regime" not in existing:
            conn.execute(text("ALTER TABLE trades ADD COLUMN regime VARCHAR"))
        if "strategy_snapshot" not in existing:
            conn.execute(text("ALTER TABLE trades ADD COLUMN strategy_snapshot TEXT"))

def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_trade_columns()

    db: Session = SessionLocal()
    try:
        portfolio = db.get(Portfolio, 1)
        if not portfolio:
            portfolio = Portfolio(
                id=1,
                balance=10000.0,
                equity=10000.0,
                daily_pnl=0.0,
                total_pnl=0.0,
                wins=0,
                losses=0,
            )
            db.add(portfolio)
            db.commit()

        performance_repository.seed_defaults(db)
    finally:
        db.close()
