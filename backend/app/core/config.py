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

    # Active Drive V2 rollout: V2 authoritative by default, V1 retained for manual rollback.
    active_drive_v2_enabled: bool = True
    active_drive_v1_available: bool = True
    default_decision_engine: str = "active_drive_v2"
    active_drive_v1_shadow_mode: bool = False
    active_drive_v2_shadow_only: bool = False
    active_drive_automatic_v1_fallback: bool = False
    active_drive_min_total_evidence: float = 8.0
    active_drive_min_point_margin: float = 4.0
    active_drive_min_confidence: float = 0.60
    active_drive_min_resolved_samples: int = 20
    active_drive_family_cap: float = 12.0

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
    position_manager_interval_seconds: int = 5

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
    # When true, the trading client targets https://testnet.binancefuture.com
    binance_futures_testnet: bool = True
    # Master server-side lock. While false, BINANCE_LIVE mode can never be
    # unlocked from the UI - requests are refused with a clear reason.
    binance_live_enabled: bool = False
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
    resolver_batch_size: int = 200
    resolver_price_disagreement_bps: float = 15.0
    resolver_allow_spot_fallback: bool = False
    resolver_max_data_age_seconds: float = 21600.0  # 6h - beyond this a candle is "stale" for resolution purposes

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
