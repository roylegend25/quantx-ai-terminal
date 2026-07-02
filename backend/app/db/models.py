from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime, timezone
from app.db.session import Base

class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True)
    side = Column(String)
    entry = Column(Float)
    exit = Column(Float, nullable=True)
    qty = Column(Float)
    status = Column(String, default="OPEN")
    sl = Column(Float, nullable=True)
    tp = Column(Float, nullable=True)
    pnl = Column(Float, default=0.0)
    opened_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    closed_at = Column(DateTime, nullable=True)

class Portfolio(Base):
    __tablename__ = "portfolio"

    id = Column(Integer, primary_key=True, default=1)
    balance = Column(Float, default=10000.0)
    equity = Column(Float, default=10000.0)
    daily_pnl = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
