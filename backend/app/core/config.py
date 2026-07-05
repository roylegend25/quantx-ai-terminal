from typing_extensions import Annotated
from pydantic_settings import BaseSettings, NoDecode
from pydantic import Field, field_validator

class Settings(BaseSettings):
    app_name: str = "QuantX AI Terminal"
    app_env: str = "production"

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
    # construct at all unless this is true. There is no live-trading flag
    # to pair it with: no adapter implements order placement.
    exchange_read_only: bool = True

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

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
