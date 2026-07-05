from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON, Boolean
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


class ResearchLabExperiment(Base):
    """One backtest run from the Phase 16 Research Lab (see app/research/lab_*
    and POST /api/research/lab/run). Distinct from ResearchExperiment above -
    that table logs a single always-on adaptive-ensemble snapshot/benchmark;
    this one logs an arbitrary, repeatable (strategy, symbol, timeframe, date
    range, parameters, fees) combination a researcher explicitly configured
    and ran, with the full trade list and equity/drawdown curves persisted so
    /montecarlo/{id} and /walkforward/{id} can replay it later. Read-only
    research tooling - nothing here feeds back into live trading."""
    __tablename__ = "research_lab_experiments"

    id = Column(Integer, primary_key=True, index=True)
    experiment_id = Column(String, unique=True, index=True)
    strategy = Column(String, index=True)  # trend | momentum | mean_reversion | breakout | ensemble | champion_ml | challenger_ml
    symbol = Column(String, index=True)
    timeframe = Column(String)
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    parameters = Column(JSON, nullable=True)  # risk knobs: atr_sl_mult, atr_tp_mult, entry_confidence_threshold, position_size_usd
    fees = Column(JSON, nullable=True)  # commission_pct, slippage_bps, spread_bps, funding_rate_pct, latency_ms, partial_fill_ratio
    results = Column(JSON, nullable=True)  # {"metrics": {...}, "trades": [...], "equity_curve": [...], "drawdown_curve": [...]}
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class StressTestRun(Base):
    """One scenario result from a POST /api/stress/run batch (see app/stress/).

    Purely a read-only fault-injection audit trail: scenarios never open,
    close, or otherwise mutate a trade, and nothing here is read back by the
    live trading path.
    """
    __tablename__ = "stress_test_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String, index=True)
    scenario_id = Column(String, index=True)
    scenario_name = Column(String)
    category = Column(String, nullable=True)
    status = Column(String)  # PASSED | FAILED
    reason = Column(Text, nullable=True)
    checks = Column(JSON, nullable=True)
    new_trades_blocked = Column(Boolean, nullable=True)
    open_positions_protected = Column(Boolean, nullable=True)
    positions_detail = Column(Text, nullable=True)
    risk_result = Column(JSON, nullable=True)
    duration_ms = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class MLOpsModel(Base):
    """One row per trained model version in the Phase 15 ML lifecycle
    platform (see app/mlops/). Independent of MLModelRegistry (app/ml/) -
    that table's Champion row is always the live adaptive ensemble and its
    Challenger rows are the one-shot backtest_models.py comparisons. This
    table tracks the full multi-status lifecycle (Training -> Testing ->
    Challenger -> Champion -> Archived / Failed) for models managed through
    /api/models. Rows are never deleted, only archived."""
    __tablename__ = "mlops_models"

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(String, unique=True, index=True)
    model_name = Column(String, index=True)
    algorithm = Column(String, nullable=True)
    version = Column(String, index=True)
    status = Column(String, default="Training", index=True)
    trained_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    promoted_at = Column(DateTime, nullable=True)
    dataset_version = Column(String, nullable=True)
    feature_version = Column(String, nullable=True)
    train_accuracy = Column(Float, nullable=True)
    val_accuracy = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)
    sharpe_ratio = Column(Float, nullable=True)
    profit_factor = Column(Float, nullable=True)
    max_drawdown_pct = Column(Float, nullable=True)
    latency_ms = Column(Float, nullable=True)
    training_duration_seconds = Column(Float, nullable=True)
    parameters = Column(JSON, nullable=True)
    feature_list = Column(JSON, nullable=True)
    git_commit = Column(String, nullable=True)
    model_path = Column(String, nullable=True)
    notes = Column(Text, nullable=True)


class MLOpsExperiment(Base):
    """One training run tracked by app/mlops/experiment.py - hyperparameters,
    seed, dataset/feature versions and resulting metrics. Distinct from
    ResearchExperiment (app/research/), which logs backtest/benchmark runs
    rather than training runs."""
    __tablename__ = "mlops_experiments"

    id = Column(Integer, primary_key=True, index=True)
    experiment_id = Column(String, unique=True, index=True)
    model_name = Column(String, index=True)
    algorithm = Column(String, nullable=True)
    model_id = Column(String, nullable=True, index=True)
    hyperparameters = Column(JSON, nullable=True)
    random_seed = Column(Integer, nullable=True)
    dataset_version = Column(String, nullable=True)
    feature_version = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    training_duration_seconds = Column(Float, nullable=True)
    status = Column(String, default="running")
    metrics = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)


class MLOpsFeatureSnapshot(Base):
    """A versioned snapshot of the feature set used for model training/drift
    comparison (app/mlops/feature_store.py). Built from data already
    computed by the live path (PredictionFeature.technical_features,
    market_context) rather than recomputing indicators."""
    __tablename__ = "mlops_feature_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    feature_version = Column(String, index=True)
    symbol = Column(String, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    features = Column(JSON, nullable=True)


class MLOpsDriftRecord(Base):
    """One drift-check result (app/mlops/drift_detector.py): feature,
    prediction, or market drift, scored via PSI or a KS test."""
    __tablename__ = "mlops_drift_records"

    id = Column(Integer, primary_key=True, index=True)
    checked_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    drift_type = Column(String, index=True)  # feature | prediction | market
    method = Column(String)  # PSI | KS
    score = Column(Float, nullable=True)
    threshold = Column(Float, nullable=True)
    is_drifted = Column(Boolean, default=False)
    details = Column(JSON, nullable=True)


class MLOpsEvaluation(Base):
    """One evaluation-history row for a MLOpsModel version
    (app/mlops/evaluation.py). Multiple rows can exist per model_id since
    evaluation can be re-run as new outcomes/drift checks come in."""
    __tablename__ = "mlops_evaluations"

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(String, index=True)
    evaluated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    accuracy = Column(Float, nullable=True)
    precision = Column(Float, nullable=True)
    recall = Column(Float, nullable=True)
    f1 = Column(Float, nullable=True)
    roc_auc = Column(Float, nullable=True)
    sharpe_ratio = Column(Float, nullable=True)
    sortino_ratio = Column(Float, nullable=True)
    profit_factor = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)
    average_r = Column(Float, nullable=True)
    max_drawdown_pct = Column(Float, nullable=True)
    expectancy = Column(Float, nullable=True)
    average_confidence = Column(Float, nullable=True)
    latency_ms = Column(Float, nullable=True)


class MLOpsRetrainRun(Base):
    """One retraining job log entry (app/mlops/retrainer.py + scheduler.py)."""
    __tablename__ = "mlops_retrain_runs"

    id = Column(Integer, primary_key=True, index=True)
    triggered_by = Column(String)  # schedule | drift | accuracy | manual
    model_name = Column(String, index=True)
    status = Column(String, default="running")  # running | success | failed | skipped
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime, nullable=True)
    result_model_id = Column(String, nullable=True)
    error = Column(Text, nullable=True)
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


class RiskSettings(Base):
    """Editable paper-trading risk limits (see app/risk/settings_repository.py).

    Singleton row (id=1). Both the /api/prediction risk gate and the
    auto-trading scheduler (app/engine/trading_engine.py) read this
    dynamically, so a PUT from the Risk Management page changes behavior on
    the very next prediction/cycle - no redeploy. paper_trading_enabled only
    ever gates this process's own paper-ledger writes; it has no bearing on
    live trading, which this codebase has no order-placement path for at all.
    """
    __tablename__ = "risk_settings"

    id = Column(Integer, primary_key=True, default=1)
    min_confidence_to_trade = Column(Float, default=0.70)
    max_risk_per_trade_pct = Column(Float, default=1.0)
    max_daily_loss_pct = Column(Float, default=2.0)
    max_weekly_loss_pct = Column(Float, default=6.0)
    max_drawdown_pct = Column(Float, default=10.0)
    max_consecutive_losses = Column(Integer, default=5)
    max_open_positions = Column(Integer, default=1)
    max_position_size_usd = Column(Float, default=1000.0)
    allow_long = Column(Boolean, default=True)
    allow_short = Column(Boolean, default=True)
    cooldown_minutes = Column(Integer, default=0)
    paper_trading_enabled = Column(Boolean, default=True)
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
