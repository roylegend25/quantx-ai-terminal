from sqlalchemy import Column, Integer, String, Float, DateTime, Text
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
    regime = Column(String, nullable=True)
    strategy_snapshot = Column(Text, nullable=True)

class StrategyWeight(Base):
    __tablename__ = "strategy_weights"

    strategy = Column(String, primary_key=True)
    weight = Column(Float, default=0.25)
    trades_json = Column(Text, default="[]")
    win_rate = Column(Float, default=0.0)
    avg_r_multiple = Column(Float, default=0.0)
    profit_factor = Column(Float, default=0.0)
    sharpe_ratio = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    avg_confidence = Column(Float, default=0.0)
    regime_performance_json = Column(Text, default="{}")
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Portfolio(Base):
    __tablename__ = "portfolio"

    id = Column(Integer, primary_key=True, default=1)
    balance = Column(Float, default=10000.0)
    equity = Column(Float, default=10000.0)
    daily_pnl = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
