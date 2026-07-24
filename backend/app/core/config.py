from typing_extensions import Annotated
from pydantic_settings import BaseSettings, NoDecode
from pydantic import Field, field_validator

class Settings(BaseSettings):
    app_name: str = "QuantX AI Terminal"
    app_env: str = "production"
    public_app_url: str = "https://www.quantxterminal.com"
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default=[
            "https://www.quantxterminal.com",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, v):
        if isinstance(v, str):
            return [origin.strip().rstrip("/") for origin in v.split(",") if origin.strip()]
        return v

    trading_mode: str = "paper"
    # Symbols the scheduler evaluates every cycle. Also settable as
    # ENABLED_SYMBOLS=BTCUSDT,ETHUSDT (comma-separated) - both env vars map
    # to the same field, ENABLED_SYMBOLS is just the more discoverable name.
    # NoDecode skips pydantic-settings' default JSON-decode-then-validate
    # path for complex (list) fields, so the comma-separated string reaches
    # the validator below untouched instead of failing json.loads().
    symbols: Annotated[list[str], NoDecode] = Field(
        default=["BTCUSDT", "ETHUSDT"], validation_alias="enabled_symbols"
    )
    default_symbol: str = "BTCUSDT"
    default_interval: str = "5m"

    # Active Drive V2 rollout: the sole decision engine in Premium X Dark.
    # The legacy ensemble-based engine (formerly Active Drive V1) has been
    # removed entirely - see the standalone QuantX Classic repository.
    active_drive_v2_enabled: bool = True
    default_decision_engine: str = "active_drive_v2"
    active_drive_v2_shadow_only: bool = False
    active_drive_min_total_evidence: float = 8.0
    active_drive_min_point_margin: float = 4.0
    active_drive_min_confidence: float = 0.60
    active_drive_min_resolved_samples: int = 20
    active_drive_family_cap: float = 12.0

    # Current-edge economics (Phase C/D/L): all rates are decimal return
    # fractions (0.0005 == 5 bps), matching the ".2%"-formatted expected_edge
    # everywhere downstream. Conservative-by-default: a real net edge must
    # clear real round-trip costs before current_edge_supported is ever True.
    active_drive_min_net_edge: float = 0.0005
    active_drive_estimated_taker_fee_rate: float = 0.0005
    active_drive_estimated_spread_bps: float = 2.0
    active_drive_estimated_slippage_bps: float = 3.0
    active_drive_estimated_funding_cost_rate: float = 0.0
    active_drive_max_edge_evidence_age_seconds: int = 86400

    # Resolution neutral band, decimal return fraction (0.0005 == 5 bps).
    # A resolved absolute return at or below this is NEUTRAL: too small to
    # be a real directional outcome once spread+slippage are considered.
    # Exact zero was the old implicit band, which made NEUTRAL impossible
    # on float returns. Neutral outcomes are excluded from the directional
    # accuracy denominator (correct stays NULL, neutral_result=True).
    resolution_neutral_band: float = 0.0005

    # Multi-exchange resolver catch-up fallback (app/data_sources/resolution_providers.py,
    # used only from app/decision_engine/resolver.py's _fetch_and_store_backfill).
    # Read-only, no order path.
    resolver_price_disagreement_bps: float = 15.0
    resolver_allow_spot_fallback: bool = False

    deployment_maintenance_mode: bool = False
    deployment_maintenance_file: str = "/app/data/deployment-maintenance"
    scheduler_startup_grace_seconds: int = 15
    execution_lease_key: str = "quantx:production:execution-lease"
    execution_lease_ttl_seconds: int = 30
    app_git_sha: str = "unknown"
    app_image_tag: str = "unknown"

    @field_validator("symbols", mode="before")
    @classmethod
    def _split_symbols(cls, v):
        if isinstance(v, str):
            return [s.strip().upper() for s in v.split(",") if s.strip()]
        return v

    confidence_threshold: float = 70.0
    max_risk_per_trade_pct: float = 0.5
    daily_loss_limit_pct: float = 2.0
    max_open_positions: int = 1

    atr_sl_mult: float = 1.5
    atr_tp_mult: float = 3.0

    scheduler_interval_seconds: int = 300
    # Main-purpose consolidation (Stage 2): the trading scheduler's own
    # 300s cycle only ever evaluates ONE execution timeframe per symbol
    # (see decision_engine/profiles.py resolve_execution_timeframe) - it
    # was never responsible for keeping every configured prediction_supported
    # timeframe's persisted decision fresh. This separate, faster loop
    # (app.trading.scheduler.observation_loop) computes+persists (never
    # executes) a decision for every other configured timeframe on its own
    # candle-close cadence, so GET /api/prediction/{symbol} can be strictly
    # read-only for any timeframe a dashboard requests.
    observation_interval_seconds: int = 60
    position_manager_interval_seconds: int = 5
    horizon_evaluation_timeout_seconds: float = 45.0
    horizon_timeframe_timeout_seconds: float = 20.0
    horizon_evaluation_concurrency: int = 3

    binance_fapi_url: str = "https://fapi.binance.com"

    # Hard safety gate for backend/app/exchanges - adapters refuse to
    # construct at all unless this is true. Real order placement lives in
    # the separate, independently gated app/exchanges/binance_futures_client
    # + app/trading/execution_router stack (Phase 22), never in adapters.
    exchange_read_only: bool = True

    # --- Phase 22: real Binance Futures trading (all OFF/paper by default) ---
    # Keys are backend-only: read from env, never persisted, never returned
    # by any endpoint.
    binance_api_key: str = ""
    binance_api_secret: str = ""
    # Phase 31: credentials for a REAL, write-capable production client are
    # deliberately a separate pair from binance_api_key/secret above (which
    # remain the read-only-monitoring + testnet-trading credential). A live
    # write client (testnet=False, read_only=False) loads ONLY this pair -
    # so normal paper/testnet operation never has live-capable credentials
    # loaded into a process at all, by construction, not merely by a flag.
    # Leave unset until deliberately provisioning live trading.
    binance_live_api_key: str = ""
    binance_live_api_secret: str = ""
    # When true, the trading client targets https://testnet.binancefuture.com
    binance_futures_testnet: bool = True
    # Master server-side lock. While false, BINANCE_LIVE mode can never be
    # unlocked from the UI - requests are refused with a clear reason.
    binance_live_enabled: bool = False
    # Phase 31: short-lived live-order authorization lease (see
    # app/trading/modes.py create_live_lease). This is independent of, and
    # in addition to, binance_live_enabled and TradingControl.mode - no
    # single one of the three can authorize a real order alone. Clamped in
    # code to a sane maximum regardless of what's configured here.
    live_lease_ttl_seconds: int = 300
    live_lease_max_actions: int = 1
    binance_default_leverage: float = 1.0
    binance_max_leverage: float = 3.0
    binance_max_notional_per_trade: float = 25.0
    binance_max_daily_loss_usdt: float = 20.0
    binance_allowed_symbols: Annotated[list[str], NoDecode] = Field(
        default=["BTCUSDT", "ETHUSDT"]
    )

    # --- Phase 27: mandatory TP/SL protection for real Binance positions ---
    # Root cause this guards against: an entry order can fill successfully
    # while its protective TP/SL orders fail afterward (seen in production:
    # Binance rejected the closePosition-style STOP_MARKET/TAKE_PROFIT_MARKET
    # with "Order type not supported for this endpoint" for this account),
    # leaving a real position open with no stop. See
    # app/trading/execution_router.py and app/trading/protection.py.
    binance_require_tp_sl: bool = True
    binance_close_if_protection_fails: bool = False
    binance_protection_watchdog_enabled: bool = True
    binance_auto_protect_positions: bool = False
    binance_block_new_trades_if_unprotected: bool = True

    # --- Phase 29: centralized Binance snapshot/rate-limit policy ---
    # Root cause this guards against: many independent backend endpoints and
    # frontend polling loops each calling Binance directly produced enough
    # duplicate REST traffic to trip Binance's 429 rate limit. See
    # app/exchanges/binance_snapshot_service.py and binance_rate_limiter.py.
    binance_snapshot_ttl_seconds: float = 3.0
    binance_orders_ttl_seconds: float = 3.0
    binance_algo_ttl_seconds: float = 8.0
    binance_income_ttl_seconds: float = 60.0
    binance_diagnostics_ttl_seconds: float = 30.0
    binance_watchdog_interval_seconds: float = 12.0
    binance_rate_limit_backoff_multiplier: float = 1.5
    binance_enable_stale_cache_on_rate_limit: bool = True

    # Multi-exchange prediction-resolver catch-up (app/decision_engine/resolver.py,
    # app/data_sources/resolution_providers.py). Read-only, no order path.
    resolver_provider_timeout: float = 10.0
    resolver_max_retries: int = 2
    resolver_batch_size: int = 50
    resolver_request_interval_seconds: float = 0.20
    resolver_claim_timeout_seconds: int = 300
    resolver_price_disagreement_bps: float = 15.0
    resolver_allow_spot_fallback: bool = False
    resolver_max_data_age_seconds: float = 21600.0  # 6h - beyond this a candle is "stale" for resolution purposes
    # Two independent queues (resolve_recent_due / resolve_historical_backfill):
    # the historical backlog runs its own larger batch on its own loop
    # interval so it can never delay a freshly-matured prediction, which
    # only ever contends with other recent rows for the small recent batch.
    resolver_recent_batch_size: int = 100
    resolver_recent_window_hours: float = 6.0
    resolver_backfill_batch_size: int = 300
    resolver_recent_interval_seconds: float = 20.0
    resolver_backfill_interval_seconds: float = 15.0
    # Indicator poor-performance/star evaluator (see
    # app.decision_engine.indicator_performance) - deliberately much less
    # frequent than the resolver ticks above: this evaluates already-
    # resolved history, not due predictions, so there's no latency pressure.
    indicator_performance_interval_seconds: float = 300.0
    indicator_performance_lookback_hours: int = 24
    indicator_performance_batch_size: int = 200
    # Main-purpose consolidation (Stage 2): re-aggregates today's and
    # yesterday's daily_* performance snapshots on this cadence. Cheap and
    # idempotent (upsert, never append), so recomputing both every tick is
    # simpler and safer than trying to detect exactly when a UTC day
    # "finalizes" - today's row stays a live-ish running total, yesterday's
    # settles once its last due prediction resolves.
    daily_aggregation_interval_seconds: float = 900.0
    # Starvation-free claim allocation (2026-07-21 recent-queue fairness fix):
    # pure newest-deadline-first claiming let a continuous stream of freshly-
    # matured predictions permanently starve older PENDING/RETRYING rows once
    # due volume exceeded one batch. This fraction of resolver_recent_batch_size
    # is reserved for the OLDEST eligible rows every cycle (guaranteeing
    # forward progress regardless of new arrivals); the remainder goes to the
    # newest rows (fast handling of just-matured predictions). Unused capacity
    # in either allocation transfers to the other within the same cycle - see
    # app.decision_engine.resolver._claim_rows_fair.
    resolver_recent_oldest_allocation_fraction: float = 0.5
    # Isolated-validation finding (2026-07-21, same fairness-fix pass): pure
    # resolution_deadline ordering inside the "oldest" allocation let a large
    # never-yet-attempted PENDING backlog itself starve RESOLUTION_ERROR_RETRYING
    # rows, since PENDING rows are frequently chronologically even older - the
    # 0.5/0.5 split above alone was not sufficient. This fraction further
    # reserves that share OF the oldest allocation specifically for RETRYING
    # rows (the remainder goes to PENDING), so neither can starve the other
    # regardless of either pool's relative size.
    resolver_recent_retrying_priority_fraction: float = 0.5

    # Decision-engine calibration (app/decision_engine/calibration.py):
    # propose_calibration_update always only PROPOSES a bounded, sample-
    # gated weight adjustment as an inactive CalibrationVersion row. This
    # flag is read by callers that would otherwise auto-apply a proposal -
    # it must stay False until offline + shadow validation passes; nothing
    # in this repair flips it on.
    calibration_auto_apply_enabled: bool = False

    @field_validator("binance_allowed_symbols", mode="before")
    @classmethod
    def _split_allowed_symbols(cls, v):
        if isinstance(v, str):
            return [s.strip().upper() for s in v.split(",") if s.strip()]
        return v

    paper_database_url: str = "sqlite:////app/data/paper.db"

    secret_key: str
    access_token_expire_minutes: int = 10080
    admin_username: str = "admin"
    admin_password_hash: str = ""

    # Phase 32: server-only master key for encrypting Binance Real API
    # credentials at rest (Fernet, from the `cryptography` package already
    # in requirements.txt). Never logged, never returned by any endpoint.
    # Empty means the credential-store endpoints refuse to save (fail
    # closed - never a silent plaintext fallback, see
    # app/security/credential_store.py).
    credential_encryption_key: str = ""

    # Phase 34: CoinGlass official liquidation-data entitlement. Empty (the
    # default - no subscription exists in this deployment) means the
    # liquidation heatmap stays on its existing Binance-derived ESTIMATED
    # model and reports that honestly; it must never be silently presented
    # as official CoinGlass data. See app/intelligence/liquidation_heatmap.py.
    coinglass_api_key: str = ""

    # --- ML lifecycle platform (app/mlops/) ---
    auto_retrain: bool = True
    mlops_retrain_schedule: str = "daily"  # daily | weekly | monthly
    mlops_scheduler_interval_seconds: int = 3600
    max_model_history: int = 20
    model_retention_days: int = 90
    champion_min_accuracy: float = 0.55
    champion_min_profit_factor: float = 1.0
    champion_min_sharpe: float = 0.0
    champion_max_drawdown_pct: float = 25.0
    champion_min_stability: float = 0.7
    champion_max_latency_ms: float = 500.0
    max_allowed_drift: float = 0.25

    # --- Optional external MLOps notification channels (app/ml_lab/
    # notifications.py). All empty by default: with no env vars set, only
    # in-app notification rows are written and no network call is ever
    # attempted. ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    notify_email_to: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
