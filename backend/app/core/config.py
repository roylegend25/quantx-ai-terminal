from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_name: str = "QuantX AI Terminal"
    app_env: str = "production"

    trading_mode: str = "paper"
    symbols: list[str] = ["BTCUSDT", "ETHUSDT"]
    default_symbol: str = "BTCUSDT"
    default_interval: str = "5m"

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
