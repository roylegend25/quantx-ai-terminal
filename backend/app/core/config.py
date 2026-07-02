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

    paper_database_url: str = "sqlite:////app/data/paper.db"

    secret_key: str
    access_token_expire_minutes: int = 10080
    admin_username: str = "admin"
    admin_password_hash: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
