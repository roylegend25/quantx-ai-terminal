from sqlalchemy.orm import Session
from app.db.session import engine, Base, SessionLocal
from app.db.models import Portfolio

def init_db():
    Base.metadata.create_all(bind=engine)

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
    finally:
        db.close()
