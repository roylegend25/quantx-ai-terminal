from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON
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
    feature_id = Column(Integer, nullable=True, index=True)


class PredictionFeature(Base):
    """A saved feature vector for one prediction, later back-filled with the
    outcome of whichever paper trade (if any) acted on it. This is the raw
    training data for future ML models - see app/ml/."""
    __tablename__ = "prediction_features"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    symbol = Column(String, index=True)
    timeframe = Column(String)
    direction = Column(String)
    confidence = Column(Float)
    regime = Column(String, nullable=True)
    feature_regime = Column(String, nullable=True)
    adaptive_strategy_weights = Column(JSON, nullable=True)
    market_context = Column(JSON, nullable=True)
    multi_timeframe_consensus = Column(JSON, nullable=True)
    technical_features = Column(JSON, nullable=True)
    decision_reason = Column(Text, nullable=True)
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    outcome = Column(String, nullable=True)  # WIN / LOSS
    realized_return = Column(Float, nullable=True)

class StrategyPerformance(Base):
    __tablename__ = "strategy_performance"

    strategy_name = Column(String, primary_key=True)
    trades = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    rolling_win_rate = Column(Float, default=0.0)
    average_r_multiple = Column(Float, default=0.0)
    profit_factor = Column(Float, default=0.0)
    sharpe_ratio = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    average_confidence = Column(Float, default=0.0)
    current_weight = Column(Float, default=0.25)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # internal bookkeeping (not part of the public field spec, but required
    # to compute rolling-window stats and per-regime breakdowns)
    trades_json = Column(Text, default="[]")
    regime_performance_json = Column(Text, default="{}")

class StrategyRollingMetrics(Base):
    """Shadow rolling-window stats for future weighting work.

    Independent of StrategyPerformance.current_weight (the value ensemble.py
    actually consumes) - writing here has no effect on live predictions.
    """
    __tablename__ = "strategy_rolling_metrics"

    strategy_name = Column(String, primary_key=True)
    trades = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    rolling_win_rate = Column(Float, default=0.0)
    average_r_multiple = Column(Float, default=0.0)
    profit_factor = Column(Float, default=0.0)
    sharpe_ratio = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    average_confidence = Column(Float, default=0.0)
    current_weight = Column(Float, default=0.25)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    trades_json = Column(Text, default="[]")
    regime_performance_json = Column(Text, default="{}")

class MLModelRegistry(Base):
    """Champion/challenger registry for offline-trained ML models (see
    app/ml/). The Champion row always represents the live adaptive ensemble
    (app/strategy/ensemble.py) - nothing in the live prediction path reads
    this table, it exists purely so trained challengers can be tracked and
    compared against production without ever being wired into it."""
    __tablename__ = "ml_model_registry"

    id = Column(Integer, primary_key=True, index=True)
    model_name = Column(String, index=True)
    version = Column(String)
    trained_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    training_samples = Column(Integer, default=0)
    validation_samples = Column(Integer, default=0)
    test_samples = Column(Integer, default=0)
    accuracy = Column(Float, nullable=True)
    precision = Column(Float, nullable=True)
    recall = Column(Float, nullable=True)
    f1 = Column(Float, nullable=True)
    auc = Column(Float, nullable=True)
    log_loss = Column(Float, nullable=True)
    calibration = Column(JSON, nullable=True)
    feature_importance = Column(JSON, nullable=True)
    status = Column(String, default="Challenger", index=True)  # Champion | Challenger | Archived
    model_path = Column(String, nullable=True)
    feature_names = Column(JSON, nullable=True)
    hyperparameters = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)


class ResearchExperiment(Base):
    """One measurable research run - a walk-forward pass, a benchmark
    comparison, or an on-demand strategy metrics snapshot (see
    app/research/). Purely an audit/analysis log: nothing here feeds back
    into live trading or prediction."""
    __tablename__ = "research_experiments"

    id = Column(Integer, primary_key=True, index=True)
    experiment_id = Column(String, unique=True, index=True)
    model_version = Column(String, nullable=True)
    strategy_version = Column(String, nullable=True)
    feature_version = Column(String, nullable=True)
    market_context_version = Column(String, nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    win_rate = Column(Float, nullable=True)
    profit_factor = Column(Float, nullable=True)
    sharpe_ratio = Column(Float, nullable=True)
    sortino_ratio = Column(Float, nullable=True)
    calmar_ratio = Column(Float, nullable=True)
    max_drawdown_pct = Column(Float, nullable=True)
    cagr_pct = Column(Float, nullable=True)
    expectancy = Column(Float, nullable=True)
    average_holding_time_seconds = Column(Float, nullable=True)
    average_confidence = Column(Float, nullable=True)
    prediction_accuracy_pct = Column(Float, nullable=True)
    raw_metrics = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)


class Portfolio(Base):
    __tablename__ = "portfolio"

    id = Column(Integer, primary_key=True, default=1)
    balance = Column(Float, default=10000.0)
    equity = Column(Float, default=10000.0)
    daily_pnl = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
