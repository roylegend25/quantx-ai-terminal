from sqlalchemy import Column, Integer, BigInteger, String, Float, DateTime, Text, JSON, Boolean, Index, UniqueConstraint, ForeignKey
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
    # --- decision provenance (why this trade was opened/closed) - all
    # nullable: rows from before this existed, and manual API opens without
    # a decision context, honestly stay NULL rather than getting backfilled.
    timeframe = Column(String, nullable=True)
    decision_mode = Column(String, nullable=True)  # active_drive_v1 | active_drive_v2 | manual
    decision_engine_version = Column(String, nullable=True)
    champion_model_id = Column(String, nullable=True)
    champion_model_type = Column(String, nullable=True)
    strategy_used = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    required_confidence = Column(Float, nullable=True)
    risk_allowed = Column(Boolean, nullable=True)
    risk_reason = Column(Text, nullable=True)
    decision_reasons = Column(JSON, nullable=True)
    model_votes = Column(JSON, nullable=True)
    close_reason = Column(Text, nullable=True)
    # --- leverage / margin / risk - all nullable: rows opened before these
    # existed honestly stay NULL and the positions API reports why instead
    # of backfilling fabricated values.
    user_id = Column(String, nullable=True)  # auth subject that opened the trade
    leverage = Column(Float, nullable=True)
    margin_mode = Column(String, nullable=True)  # isolated (only supported mode)
    margin_used = Column(Float, nullable=True)  # initial margin committed at entry, USDT
    maintenance_margin_rate = Column(Float, nullable=True)  # flat rate stamped at open
    liquidation_price = Column(Float, nullable=True)  # ESTIMATED (see app/trading/margin.py)
    trailing_stop = Column(Float, nullable=True)  # trailing distance in price units
    realized_pnl = Column(Float, nullable=True)  # accumulates across partial closes
    updated_at = Column(DateTime, nullable=True)
    # --- automated-entry provenance (Trading Horizon) - all nullable: a
    # manual/direct API open has no decision or authority behind it and
    # honestly stays NULL rather than fabricating a link.
    decision_id = Column(String, nullable=True, index=True)  # ActiveDriveDecision.decision_id that authorized entry
    authority_id = Column(String, nullable=True, index=True)  # TradingHorizonDecision.id consumed to open this trade
    execution_mode = Column(String, nullable=True)  # "automatic" (Trading Horizon) | "manual" (direct API open)
    edge_at_entry = Column(Float, nullable=True)  # net_expected_edge from the authorizing decision at entry time


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
    target = Column(Float, nullable=True)
    stop = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    outcome = Column(String, nullable=True)  # WIN / LOSS
    realized_return = Column(Float, nullable=True)
    latency_ms = Column(Float, nullable=True)

class UserBotSetting(Base):
    __tablename__ = "user_bot_settings"
    user_id = Column(String, primary_key=True)
    decision_engine = Column(String, nullable=False, default="active_drive_v2")
    compare_engines_shadow = Column(Boolean, nullable=False, default=False)
    trading_profile = Column(String, nullable=False, default="auto_adaptive")
    strict_timeframe_unanimity = Column(Boolean, nullable=False, default=True)
    auto_profile_enabled = Column(Boolean, nullable=False, default=True)
    profile_updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    profile_revision = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class DecisionEngineChange(Base):
    __tablename__ = "decision_engine_changes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    previous_engine = Column(String, nullable=False)
    new_engine = Column(String, nullable=False)
    changed_by = Column(String, nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

class ActiveDriveDecision(Base):
    __tablename__ = "active_drive_decisions"
    __table_args__ = (Index("ix_active_drive_scope_time", "user_id", "symbol", "timeframe", "created_at"),)
    decision_id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    engine = Column(String, nullable=False, index=True)
    engine_version = Column(String, nullable=False)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    signal = Column(String, nullable=False)
    long_points = Column(Float, nullable=False, default=0.0)
    short_points = Column(Float, nullable=False, default=0.0)
    confidence = Column(Float, nullable=False, default=0.0)
    expected_edge = Column(Float, nullable=True)
    gross_expected_edge = Column(Float, nullable=True)
    net_expected_edge = Column(Float, nullable=True)
    edge_supported = Column(Boolean, nullable=True)
    edge_block_reason = Column(String, nullable=True)
    edge_sample_size = Column(Integer, nullable=True)
    edge_source = Column(String, nullable=True)
    eligible_for_execution = Column(Boolean, nullable=False, default=False)
    blocking_reasons = Column(JSON, nullable=False, default=list)
    decision_payload = Column(JSON, nullable=False, default=dict)
    shadow = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class TradingHorizonDecision(Base):
    """Immutable, server-issued authority for one possible new entry."""
    __tablename__ = "trading_horizon_decisions"
    id = Column(String, primary_key=True)
    # Historical authorities created before the fingerprint migration remain
    # NULL; every newly issued authority is required to provide a fingerprint.
    issuance_fingerprint = Column(String, nullable=True, unique=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    selected_profile = Column(String, nullable=False)
    profile_revision = Column(Integer, nullable=False)
    authoritative_execution_timeframe = Column(String, nullable=False)
    final_direction = Column(String, nullable=False)
    engine_id = Column(String, nullable=False)
    engine_version = Column(String, nullable=False)
    unanimous = Column(Boolean, nullable=False)
    readiness = Column(Boolean, nullable=False)
    execution_eligible = Column(Boolean, nullable=False)
    generated_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    market_data_revision = Column(String, nullable=False)
    performance_snapshot_revision = Column(String, nullable=False)
    expected_edge = Column(Float, nullable=True)
    directional_confidence = Column(Float, nullable=True)
    point_margin = Column(Float, nullable=True)
    evidence = Column(JSON, nullable=False, default=dict)
    blocking_reasons = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class TradingHorizonTimeframeLink(Base):
    """Immutable proof linking horizon authority to persisted V2 decisions."""
    __tablename__ = "trading_horizon_timeframe_links"
    __table_args__ = (
        UniqueConstraint("horizon_decision_id", "timeframe", "role", name="uq_horizon_timeframe_role"),
    )
    id = Column(Integer, primary_key=True)
    horizon_decision_id = Column(String, ForeignKey("trading_horizon_decisions.id"), nullable=False, index=True)
    decision_id = Column(String, ForeignKey("active_drive_decisions.decision_id"), nullable=False, index=True)
    timeframe = Column(String, nullable=False)
    role = Column(String, nullable=False)
    direction = Column(String, nullable=False)
    confidence = Column(Float, nullable=True)
    eligible = Column(Boolean, nullable=False)
    engine_id = Column(String, nullable=False)
    engine_version = Column(String, nullable=False)
    generated_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class TradingHorizonConsumption(Base):
    __tablename__ = "trading_horizon_consumptions"
    horizon_decision_id = Column(String, ForeignKey("trading_horizon_decisions.id"), primary_key=True)
    idempotency_key = Column(String, nullable=False, unique=True)
    consumed_at = Column(DateTime, nullable=False)

class SignalCandidateRecord(Base):
    __tablename__ = "signal_candidates"
    __table_args__ = (Index("ix_signal_candidate_scope", "source_name", "source_version", "symbol", "timeframe", "created_at"),)
    id = Column(String, primary_key=True)
    decision_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    source_type = Column(String, nullable=False)
    source_family = Column(String, nullable=False)
    source_name = Column(String, nullable=False, index=True)
    source_version = Column(String, nullable=False)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    direction = Column(String, nullable=False)
    probability_up = Column(Float, nullable=True)
    probability_down = Column(Float, nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    candidate_points = Column(Float, nullable=False, default=0.0)
    expected_edge = Column(Float, nullable=True)
    risk_reward_ratio = Column(Float, nullable=True)
    market_regime = Column(String, nullable=True, index=True)
    evidence_tier = Column(String, nullable=False, default="insufficient_evidence")
    resolved_sample_size = Column(Integer, nullable=False, default=0)
    historical_accuracy = Column(Float, nullable=True)
    eligible = Column(Boolean, nullable=False, default=False)
    rejection_reason = Column(Text, nullable=True)
    evidence = Column(JSON, nullable=False, default=dict)
    data_freshness = Column(String, nullable=False, default="live")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

class PredictionCycle(Base):
    """User-initiated prediction cycle marker. Starting a new cycle never
    deletes or rewrites history - older ledger rows simply keep their old
    cycle_id (or NULL for rows that predate cycles) and dashboards can
    filter to the current cycle for a fresh-start view."""
    __tablename__ = "prediction_cycles"
    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    label = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=True, unique=True, index=True)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class PredictionLedger(Base):
    __tablename__ = "prediction_ledger"
    __table_args__ = (Index("ix_prediction_ledger_scope", "source_name", "source_version", "symbol", "timeframe", "generated_at"), UniqueConstraint("candidate_id", name="uq_prediction_ledger_candidate"))
    prediction_id = Column(String, primary_key=True)
    cycle_id = Column(String, nullable=True, index=True)
    candidate_id = Column(String, nullable=False)
    decision_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    engine = Column(String, nullable=False)
    engine_version = Column(String, nullable=False)
    source_type = Column(String, nullable=False)
    source_name = Column(String, nullable=False, index=True)
    source_version = Column(String, nullable=False)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    market_regime = Column(String, nullable=True, index=True)
    direction = Column(String, nullable=False)
    probability_up = Column(Float, nullable=True)
    probability_down = Column(Float, nullable=True)
    confidence = Column(Float, nullable=False)
    points = Column(Float, nullable=False, default=0.0)
    expected_edge = Column(Float, nullable=True)
    reference_price = Column(Float, nullable=True)
    target_reference_price = Column(Float, nullable=True)
    stop_reference_price = Column(Float, nullable=True)
    data_revision = Column(String, nullable=True)
    target_horizon_seconds = Column(Integer, nullable=False)
    resolution_deadline = Column(DateTime, nullable=False, index=True)
    feature_snapshot_hash = Column(String, nullable=False)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

class PredictionResolution(Base):
    __tablename__ = "prediction_resolutions"
    id = Column(Integer, primary_key=True, index=True)
    prediction_id = Column(String, nullable=False, unique=True, index=True)
    actual_return = Column(Float, nullable=False)
    resolved_direction = Column(String, nullable=False)
    correct = Column(Boolean, nullable=True)
    neutral_result = Column(Boolean, nullable=False, default=False)
    target_hit = Column(Boolean, nullable=True)
    stop_hit = Column(Boolean, nullable=True)
    maximum_favorable_excursion = Column(Float, nullable=True)
    maximum_adverse_excursion = Column(Float, nullable=True)
    resolution_reason = Column(String, nullable=False)
    resolved_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

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
    # --- AI Model Lab additions (app/ml_lab/) - all nullable so rows written
    # by the older retrainer path stay valid without backfill ---
    model_size_bytes = Column(Integer, nullable=True)
    training_samples = Column(Integer, nullable=True)
    test_samples = Column(Integer, nullable=True)
    dataset_source = Column(String, nullable=True)  # market_history | trade_outcomes
    dataset_spec = Column(JSON, nullable=True)
    precision = Column(Float, nullable=True)
    recall = Column(Float, nullable=True)
    f1 = Column(Float, nullable=True)
    roc_auc = Column(Float, nullable=True)
    avg_confidence = Column(Float, nullable=True)
    avg_prediction_error = Column(Float, nullable=True)
    total_trades = Column(Integer, nullable=True)
    inference_rows_per_sec = Column(Float, nullable=True)
    peak_memory_mb = Column(Float, nullable=True)
    cpu_info = Column(String, nullable=True)
    gpu_info = Column(String, nullable=True)
    # mean out-of-sample accuracy across walk-forward folds
    # (app/ml_lab/walk_forward.py); NULL for versions trained before the
    # walk-forward step existed or for sequence models where it is skipped
    oos_accuracy = Column(Float, nullable=True)


class MLTrainingJob(Base):
    """One asynchronous training/HPO job run by app/ml_lab/jobs.py in a
    separate worker process. The `progress` JSON is rewritten by the worker
    as training advances (epoch, loss, eta, cpu/ram, samples/sec...) so the
    UI can poll it without any state living in the API process - a restart
    mid-job leaves an honest 'running' row whose dead pid gets reaped to
    'failed' on the next read."""
    __tablename__ = "ml_training_jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String, unique=True, index=True)
    kind = Column(String, default="train")  # train | hpo
    algorithm = Column(String, index=True)
    model_name = Column(String, index=True)
    params = Column(JSON, nullable=True)  # dataset spec + hyperparameters / hpo config
    status = Column(String, default="queued", index=True)  # queued|running|succeeded|failed|cancelled
    progress = Column(JSON, nullable=True)
    result_model_id = Column(String, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    pid = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class MLModelArtifact(Base):
    """Evaluation artifacts for one MLOpsModel version, one row per kind:
    confusion_matrix, roc_curve, pr_curve, calibration_curve, lift_gain,
    importance (native SHAP summary or permutation importance),
    shap_sample (per-row contributions for beeswarm plots),
    training_curve, hpo_trials. Pure JSON payloads computed from real
    holdout data by app/ml_lab/runner.py - never synthesized after the
    fact."""
    __tablename__ = "ml_model_artifacts"

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(String, index=True)
    kind = Column(String, index=True)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MLNotification(Base):
    """In-app MLOps notification feed (app/ml_lab/notifications.py): one row
    per lifecycle event - training started/completed/failed, challenger
    created/rejected, champion promoted/rolled back, drift and data-quality
    warnings. Rows are the source of truth for the UI bell; external
    channels (Telegram/Discord/SMTP) are best-effort mirrors of these rows,
    never the other way around."""
    __tablename__ = "ml_notifications"

    id = Column(Integer, primary_key=True, index=True)
    event = Column(String, index=True)  # training_started | training_completed | ...
    severity = Column(String, default="info", index=True)  # info | success | warning | error
    title = Column(String)
    message = Column(Text, nullable=True)
    data = Column(JSON, nullable=True)
    read = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class MLLabSettings(Base):
    """Singleton row (id=1) of dashboard-editable AI-lab configuration:
    automatic-promotion thresholds and the retraining schedule/drift
    trigger (app/ml_lab/settings_repo.py)."""
    __tablename__ = "ml_lab_settings"

    id = Column(Integer, primary_key=True, default=1)
    data = Column(JSON, nullable=True)
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


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


# --------------------------------------------------------------------------
# Phase 20 - Real-World Data Engine (app/data_sources/). All timestamps for
# market data are epoch milliseconds (matching the "time" convention the
# chart/backtest CSVs already use); created_at is a wall-clock audit column.
# Every row carries provider + quality_score so a consumer can always tell
# where a datapoint came from and how trustworthy its dataset was.
# --------------------------------------------------------------------------

class MarketCandle(Base):
    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", "provider", name="uq_market_candles_key"),
        Index("ix_market_candles_lookup", "symbol", "timeframe", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    timeframe = Column(String, index=True, nullable=False)
    timestamp = Column(BigInteger, index=True, nullable=False)  # candle open time, epoch ms
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    quote_volume = Column(Float, nullable=True)
    trades_count = Column(Integer, nullable=True)
    interpolated = Column(Boolean, default=False)  # filled by app/data_sources/interpolator.py, never by a provider
    provider = Column(String, default="binance_futures")
    quality_score = Column(Float, nullable=True)  # dataset-level score stamped at download time
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FundingRateRow(Base):
    __tablename__ = "funding_rates"
    __table_args__ = (
        UniqueConstraint("symbol", "timestamp", "provider", name="uq_funding_rates_key"),
        Index("ix_funding_rates_lookup", "symbol", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    timeframe = Column(String, default="8h")  # funding period
    timestamp = Column(BigInteger, index=True, nullable=False)  # funding time, epoch ms
    funding_rate = Column(Float, nullable=False)
    mark_price = Column(Float, nullable=True)
    provider = Column(String, default="binance_futures")
    quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class OpenInterestRow(Base):
    __tablename__ = "open_interest_history"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", "provider", name="uq_open_interest_key"),
        Index("ix_open_interest_lookup", "symbol", "timeframe", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    timeframe = Column(String, index=True, nullable=False)  # sampling period (5m/15m/...)
    timestamp = Column(BigInteger, index=True, nullable=False)
    open_interest = Column(Float, nullable=False)  # contracts
    open_interest_value = Column(Float, nullable=True)  # USD notional
    provider = Column(String, default="binance_futures")
    quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class OrderbookSnapshotRow(Base):
    __tablename__ = "orderbook_snapshots"
    __table_args__ = (Index("ix_orderbook_snapshots_lookup", "symbol", "timestamp"),)

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    timeframe = Column(String, default="tick")  # snapshots are point-in-time, not bar-aligned
    timestamp = Column(BigInteger, index=True, nullable=False)
    best_bid = Column(Float, nullable=True)
    best_ask = Column(Float, nullable=True)
    spread_bps = Column(Float, nullable=True)
    bid_depth_usd = Column(Float, nullable=True)
    ask_depth_usd = Column(Float, nullable=True)
    imbalance = Column(Float, nullable=True)  # (bid-ask)/(bid+ask) depth, [-1, 1]
    levels = Column(JSON, nullable=True)  # top-of-book ladder actually fetched
    provider = Column(String, default="binance_futures")
    quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TradeTick(Base):
    __tablename__ = "trade_ticks"
    __table_args__ = (
        UniqueConstraint("symbol", "provider", "trade_id", name="uq_trade_ticks_key"),
        Index("ix_trade_ticks_lookup", "symbol", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    timeframe = Column(String, default="tick")
    timestamp = Column(BigInteger, index=True, nullable=False)
    trade_id = Column(BigInteger, nullable=True)  # provider aggTrade id, for dedup
    price = Column(Float, nullable=False)
    qty = Column(Float, nullable=False)
    side = Column(String, nullable=True)  # BUY / SELL (taker side)
    provider = Column(String, default="binance_futures")
    quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class LiquidationEstimateRow(Base):
    __tablename__ = "liquidation_estimates"
    __table_args__ = (Index("ix_liquidation_estimates_lookup", "symbol", "timeframe", "timestamp"),)

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    timeframe = Column(String, index=True, nullable=False)
    timestamp = Column(BigInteger, index=True, nullable=False)
    side = Column(String, nullable=True)  # LONG / SHORT liquidations
    price_level = Column(Float, nullable=True)
    strength = Column(Float, nullable=True)  # 0-100 relative cluster strength
    notional_usd = Column(Float, nullable=True)
    method = Column(String, nullable=True)  # "coinglass" (measured) | "oi_wick_estimate" (derived)
    provider = Column(String, default="derived")
    quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SentimentRow(Base):
    __tablename__ = "sentiment_history"
    __table_args__ = (
        UniqueConstraint("metric", "timestamp", "provider", name="uq_sentiment_key"),
        Index("ix_sentiment_lookup", "metric", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, nullable=True, index=True)  # market-wide metrics leave this NULL
    timeframe = Column(String, default="1d")
    timestamp = Column(BigInteger, index=True, nullable=False)
    metric = Column(String, index=True, nullable=False)  # fear_greed | btc_dominance | stablecoin_dominance
    value = Column(Float, nullable=False)
    label = Column(String, nullable=True)  # e.g. "Extreme Fear"
    provider = Column(String, default="alternative.me")
    quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DataDownloadJob(Base):
    __tablename__ = "data_download_jobs"
    __table_args__ = (Index("ix_data_jobs_lookup", "symbol", "timeframe", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String, unique=True, index=True)
    data_type = Column(String, index=True)  # candles | funding | open_interest | orderbook | trades | sentiment | liquidations
    symbol = Column(String, index=True, nullable=True)
    timeframe = Column(String, index=True, nullable=True)
    provider = Column(String, nullable=True)
    status = Column(String, default="queued", index=True)  # queued | running | succeeded | failed
    requested_start = Column(BigInteger, nullable=True)
    requested_end = Column(BigInteger, nullable=True)
    rows_fetched = Column(Integer, default=0)
    rows_stored = Column(Integer, default=0)
    rows_interpolated = Column(Integer, default=0)
    rows_rejected = Column(Integer, default=0)
    quality_score = Column(Float, nullable=True)
    error = Column(Text, nullable=True)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class DataQualityReport(Base):
    __tablename__ = "data_quality_reports"
    __table_args__ = (Index("ix_data_quality_lookup", "symbol", "timeframe", "timestamp"),)

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    timeframe = Column(String, index=True, nullable=False)
    data_type = Column(String, default="candles")
    timestamp = Column(BigInteger, index=True, nullable=False)  # when the check ran, epoch ms
    range_start = Column(BigInteger, nullable=True)
    range_end = Column(BigInteger, nullable=True)
    rows = Column(Integer, default=0)
    expected_rows = Column(Integer, nullable=True)
    missing_candles = Column(Integer, default=0)
    duplicates = Column(Integer, default=0)
    zero_volume = Column(Integer, default=0)
    broken_ohlc = Column(Integer, default=0)
    outliers = Column(Integer, default=0)
    interpolated = Column(Integer, default=0)
    rejected_gaps = Column(Integer, default=0)
    largest_gap_bars = Column(Integer, default=0)
    stale = Column(Boolean, default=False)
    quality_score = Column(Float, nullable=True)  # 0-100
    gaps = Column(JSON, nullable=True)  # [{"start": ms, "end": ms, "bars": n, "action": "interpolated"|"rejected"}]
    details = Column(JSON, nullable=True)
    provider = Column(String, default="binance_futures")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class FeatureSnapshotRow(Base):
    __tablename__ = "feature_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", "feature_version", name="uq_feature_snapshots_key"),
        Index("ix_feature_snapshots_lookup", "symbol", "timeframe", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    timeframe = Column(String, index=True, nullable=False)
    timestamp = Column(BigInteger, index=True, nullable=False)  # candle open time the features describe
    feature_version = Column(String, default="v1")
    features = Column(JSON, nullable=False)
    provider = Column(String, default="binance_futures")
    quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AdvancedBacktestRun(Base):
    """One POST /api/backtest/run-advanced execution: possibly multi-symbol,
    multi-timeframe, with optional out-of-sample split, walk-forward and
    Monte Carlo stages. The full request + per-combination results are stored
    as JSON so GET /api/backtest/results/{id}, /walkforward/{id} and
    /montecarlo/{id} can replay it later without recomputing."""
    __tablename__ = "advanced_backtest_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String, unique=True, index=True)
    strategy = Column(String, index=True)
    symbols = Column(JSON, nullable=True)
    timeframes = Column(JSON, nullable=True)
    preset = Column(String, nullable=True)
    status = Column(String, default="succeeded")
    request = Column(JSON, nullable=True)
    results = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class LearningEvaluation(Base):
    """One POST /api/learning/evaluate aggregate snapshot: how accurate stored
    predictions turned out against real recorded outcomes (paper-trade exits
    where a trade acted; otherwise the actual later close from
    market_candles). Read-only research output - nothing here mutates live
    strategy weights or the prediction path."""
    __tablename__ = "learning_evaluations"

    id = Column(Integer, primary_key=True, index=True)
    evaluated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    symbol = Column(String, nullable=True, index=True)
    predictions_considered = Column(Integer, default=0)
    predictions_resolved = Column(Integer, default=0)
    direction_hit_rate_pct = Column(Float, nullable=True)
    avg_error_pct = Column(Float, nullable=True)
    avg_confidence = Column(Float, nullable=True)
    confidence_reliability = Column(JSON, nullable=True)  # calibration buckets
    by_timeframe = Column(JSON, nullable=True)
    by_regime = Column(JSON, nullable=True)
    by_direction = Column(JSON, nullable=True)
    details = Column(JSON, nullable=True)


class Portfolio(Base):
    __tablename__ = "portfolio"

    id = Column(Integer, primary_key=True, default=1)
    balance = Column(Float, default=10000.0)
    equity = Column(Float, default=10000.0)
    daily_pnl = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)


class TradingControl(Base):
    """Singleton row (id=1) of runtime trading-mode state (Phase 22, see
    app/trading/modes.py). The requested mode can only ever be one of
    PAPER | BINANCE_TESTNET | BINANCE_LIVE; BINANCE_LIVE_LOCKED is never
    stored - it is computed (live requested but env lock or UI unlock
    missing). live_unlocked is set only by the explicit unlock ceremony and
    means nothing while BINANCE_LIVE_ENABLED=false on the server."""
    __tablename__ = "trading_control"

    id = Column(Integer, primary_key=True, default=1)
    mode = Column(String, default="PAPER")
    live_unlocked = Column(Boolean, default=False)
    live_unlocked_at = Column(DateTime, nullable=True)
    kill_switch_active = Column(Boolean, default=False)
    kill_switch_reason = Column(Text, nullable=True)
    kill_switch_at = Column(DateTime, nullable=True)
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class ExchangePositionRow(Base):
    """Local mirror of one open Binance Futures position (Phase 22, written
    only by app/trading/execution_router.py sync). Binance is the source of
    truth - these rows exist so the UI/audit trail can show what the bot
    believed was open, and so a local-vs-exchange disagreement can raise a
    sync warning that blocks new orders until reconciled."""
    __tablename__ = "exchange_positions"

    id = Column(Integer, primary_key=True, index=True)
    mode = Column(String, index=True)  # BINANCE_TESTNET | BINANCE_LIVE
    symbol = Column(String, index=True)
    side = Column(String)
    quantity = Column(Float)
    entry_price = Column(Float, nullable=True)
    mark_price = Column(Float, nullable=True)
    leverage = Column(Float, nullable=True)
    margin_type = Column(String, nullable=True)
    liquidation_price = Column(Float, nullable=True)
    unrealized_pnl = Column(Float, nullable=True)
    margin_used = Column(Float, nullable=True)
    notional = Column(Float, nullable=True)
    stop_loss_order_id = Column(String, nullable=True)
    take_profit_order_id = Column(String, nullable=True)
    exchange_position_id = Column(String, nullable=True)
    # Phase 26: which protection surface this position's TP/SL were placed
    # through - "classic" (POST /fapi/v1/order) or "algo" (POST
    # /fapi/v1/algoOrder). Algo orders never appear in GET /fapi/v1/
    # openOrders, so their ids are tracked here (not derivable from Binance
    # any other way for this account - see app.exchanges.binance_algo_provider)
    # and re-verified individually by id, never via a "list" call.
    protection_provider = Column(String, nullable=True)
    tp_algo_id = Column(BigInteger, nullable=True)
    sl_algo_id = Column(BigInteger, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Phase 30: the last CONFIRMED protection verdict for this position,
    # persisted so it survives a restart/reload instead of only existing as
    # a return value of the last resolve_protection() call. protection_
    # revision increments on every confirmed mutation (place/edit/repair) -
    # see app.trading.execution_router._store_protection_metadata /
    # _store_protective_order_id - so a cache or in-flight read started
    # before a mutation can never overwrite a newer confirmed result with a
    # stale one (compare revisions, higher always wins).
    protection_status = Column(String, nullable=True)
    protection_revision = Column(Integer, nullable=True, default=0)
    protection_verified_at = Column(DateTime, nullable=True)


class BinanceBotTrade(Base):
    """One REAL Binance order placed by this system (Phase 23) - the bot's
    own journal, kept strictly separate from the paper Trade table and from
    manual/synced exchange history. Rows are written only by
    BinanceExecutionProvider after the exchange accepted the order, with the
    full decision provenance and risk-gate outcome attached. label is always
    BOT_TRADE here; exchange history rows that don't match one of these
    order ids are labelled SYNCED_FROM_BINANCE / MANUAL_EXCHANGE_TRADE at
    read time and never stored in this table."""
    __tablename__ = "binance_bot_trades"

    id = Column(Integer, primary_key=True, index=True)
    mode = Column(String, index=True)  # BINANCE_TESTNET (internal) | BINANCE_LIVE
    action = Column(String, index=True)  # open | close
    order_id = Column(BigInteger, index=True)
    client_order_id = Column(String, index=True)
    symbol = Column(String, index=True)
    side = Column(String)  # LONG | SHORT (position side)
    order_side = Column(String)  # BUY | SELL (exchange order side)
    order_type = Column(String)
    quantity = Column(Float)
    avg_fill_price = Column(Float, nullable=True)
    status = Column(String)
    reduce_only = Column(Boolean, default=False)
    notional = Column(Float, nullable=True)
    leverage = Column(Float, nullable=True)
    sl_order_id = Column(BigInteger, nullable=True)
    tp_order_id = Column(BigInteger, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    label = Column(String, default="BOT_TRADE")
    # decision provenance
    confidence = Column(Float, nullable=True)
    decision_engine = Column(String, nullable=True, index=True)
    decision_engine_version = Column(String, nullable=True)
    strategy = Column(String, nullable=True)
    model = Column(String, nullable=True)
    decision_reason = Column(Text, nullable=True)
    risk_gate = Column(JSON, nullable=True)  # the checks the real risk gate ran
    realized_pnl = Column(Float, nullable=True)  # backfilled from income where matched
    # Phase 27: TP/SL protection provenance - see app/trading/protection.py.
    # protection_status mirrors that module's vocabulary (PROTECTED |
    # MISSING_TP | MISSING_SL | MISSING_TP_SL), verified against Binance's
    # own open orders, never assumed from the placement call succeeding.
    entry_order_id = Column(BigInteger, nullable=True)
    protection_status = Column(String, nullable=True, index=True)
    protection_failed_reason = Column(Text, nullable=True)
    protection_confirmed_at = Column(DateTime, nullable=True)
    exit_order_id = Column(BigInteger, nullable=True)
    exit_reason = Column(Text, nullable=True)
    # Phase 26: which protection provider handled this trade's TP/SL, and
    # the raw (already-redacted) evidence of how it went - see
    # app.trading.protection_provider.
    provider_used = Column(String, nullable=True)
    provider_response = Column(JSON, nullable=True)
    provider_latency_ms = Column(Float, nullable=True)
    verification_result = Column(String, nullable=True)
    repair_attempts = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class TradingAuditLog(Base):
    """Append-only audit trail of every real-trading lifecycle event (Phase
    22): order requested/accepted/rejected/filled/canceled, TP/SL updates,
    position syncs, mode changes, live unlock, kill switch. Persisted (the
    /api/logs ring buffer is in-memory only) and never contains API secrets
    - see app/trading/modes.py:audit()."""
    __tablename__ = "trading_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    event = Column(String, index=True)
    mode = Column(String, index=True, nullable=True)
    symbol = Column(String, index=True, nullable=True)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class ExecutionIntentLock(Base):
    """Cross-worker new-entry mutex. One active intent per user/symbol/engine."""
    __tablename__ = "execution_intent_locks"
    scope_key = Column(String, primary_key=True)
    idempotency_key = Column(String, nullable=False, unique=True, index=True)
    owner_token = Column(String, nullable=False)
    fencing_token = Column(Integer, nullable=False)
    heartbeat_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class ExecutionIntentAudit(Base):
    """Durable idempotency outcome and authority provenance for every intent."""
    __tablename__ = "execution_intent_audit"
    id = Column(Integer, primary_key=True, index=True)
    idempotency_key = Column(String, nullable=False, unique=True, index=True)
    scope_key = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    engine = Column(String, nullable=False)
    profile_decision_id = Column(String, nullable=False)
    execution_timeframe = Column(String, nullable=False)
    direction = Column(String, nullable=False)
    status = Column(String, nullable=False, index=True)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    completed_at = Column(DateTime, nullable=True)


class ExecutionFenceCounter(Base):
    __tablename__ = "execution_fence_counters"
    scope_key = Column(String, primary_key=True)
    current_token = Column(Integer, nullable=False, default=0)


class BinanceExecutionAttempt(Base):
    """One row per BinanceExecutionProvider.open_position() call (Phase 25 -
    execution transparency). Unlike BinanceBotTrade (written only once an
    order is actually accepted by the exchange), this is written for EVERY
    attempt regardless of outcome, with a stage-by-stage breakdown of the
    pipeline (signal_generated -> champion_model -> risk_gate ->
    position_sizing -> quantity_calculation -> binance_validation ->
    order_submitted -> order_accepted) so a silent execution failure after
    the decision engine already said "allowed" is never invisible. Never
    contains API keys/secrets - exchange_response is the same safe,
    classified text used everywhere else (see execution_router._safe_error
    and _classify_execution_failure)."""
    __tablename__ = "binance_execution_attempts"

    id = Column(Integer, primary_key=True, index=True)
    mode = Column(String, index=True)
    symbol = Column(String, index=True)
    side = Column(String, nullable=True)
    is_test = Column(Boolean, default=False, index=True)
    confidence = Column(Float, nullable=True)
    requested_notional = Column(Float, nullable=True)
    requested_quantity = Column(Float, nullable=True)
    adjusted_quantity = Column(Float, nullable=True)
    leverage = Column(Float, nullable=True)
    margin_required = Column(Float, nullable=True)
    # ordered list of {stage, status: success|waiting|failed, reason, at}
    stages = Column(JSON, nullable=False)
    final_status = Column(String, index=True)  # order_accepted | failed
    final_reason = Column(Text, nullable=True)
    exchange_response = Column(JSON, nullable=True)
    order_id = Column(BigInteger, nullable=True)
    latency_ms = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


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
    # Phase 26 - Live Margin Calculator's advisory safety reserve (never
    # consulted by real_risk_gate.py, which continues to rely on Binance's
    # own balance check - these only feed the margin calculator's
    # recommended-max-notional math and the "Auto Configure Risk" button).
    safety_buffer_usdt = Column(Float, default=1.0)
    safety_buffer_pct = Column(Float, default=0.10)
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class BinanceProtectionCapability(Base):
    """Which TP/SL protection surface a given mode's account is known to
    require - "classic" (POST /fapi/v1/order) or "algo" (POST /fapi/v1/
    algoOrder), see app.trading.protection_provider. One row per mode
    (BINANCE_TESTNET / BINANCE_LIVE): testnet and live are different
    accounts and may genuinely differ. Written once classic protection
    succeeds, or the instant classic is rejected with code -4120
    (STOP_ORDER_SWITCH_ALGO) - every order after that first detection skips
    straight to the known-working path instead of re-discovering it."""
    __tablename__ = "binance_protection_capability"

    id = Column(Integer, primary_key=True, index=True)
    mode = Column(String, unique=True, index=True)  # BINANCE_TESTNET | BINANCE_LIVE
    provider = Column(String)  # classic | algo
    detected_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    detection_reason = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
