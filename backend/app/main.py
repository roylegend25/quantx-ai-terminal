from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
import asyncio
from app.api.auth import router as auth_router
from app.api.market import router as market_router
from app.api.dashboard import router as dashboard_router
from app.api.quant import router as quant_router
from app.api.prediction import router as prediction_router
from app.api.bot import router as bot_router
from app.api.orderbook import router as orderbook_router
from app.api.trades import router as trades_router
from app.api.paper import router as paper_router
from app.api.ws import router as ws_router
from app.api.backtest import router as backtest_router
from app.api.strategy import router as strategy_router
from app.api.timeframes import router as timeframes_router
from app.api.ml import router as ml_router
from app.api.research import router as research_router
from app.api.stress import router as stress_router
from app.api.exchange import router as exchange_router
from app.api.execution import router as execution_router
from app.monitoring.health import router as health_router
from app.monitoring.logging import RequestLoggingMiddleware
from app.monitoring.metrics import PrometheusMiddleware, instrument_db_engine
from datetime import datetime, timezone
from app.core.deps import get_current_user
from app.db.init_db import init_db
from app.db.session import engine as db_engine
from app.trading.scheduler import start_scheduler
from app.trading.position_manager import start_position_manager
from app.mlops.scheduler import start_scheduler as start_mlops_scheduler
from app.api.models import router as models_router
from app.api.research_lab import router as research_lab_router
from app.api.logs import router as logs_router
from app.api.risk import router as risk_router
from app.api.data import router as data_router
from app.api.learning import router as learning_router
from app.api.portfolio import router as portfolio_router
from app.api.trading_control import router as trading_control_router
from app.api.bot_trades import router as bot_trades_router
from app.api.admin_config import router as admin_config_router
from app.core.env_manager import apply_to_settings as apply_env_file_to_settings
from app.data_sources.scheduler import start_data_scheduler
from app.trading.binance_sync import start_binance_sync
from app.trading.protection_watchdog import start_protection_watchdog

app = FastAPI(title="QuantX AI Terminal API", version="2.0.0")

async def delayed_background_start():
    await asyncio.sleep(5)
    start_scheduler()
    start_position_manager()
    start_mlops_scheduler()
    start_data_scheduler()
    start_binance_sync()
    start_protection_watchdog()

@app.on_event("startup")
async def startup_event():
    # The mounted .env file is authoritative for the admin-editable trading
    # keys: compose injects env vars at container CREATE time, so without
    # this a plain restart would resurrect stale values over an edit made
    # through /api/admin/server-config.
    apply_env_file_to_settings()
    init_db()
    instrument_db_engine(db_engine)
    asyncio.create_task(delayed_background_start())

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(PrometheusMiddleware)

protected = [Depends(get_current_user)]

app.include_router(auth_router)
app.include_router(market_router, dependencies=protected)
app.include_router(dashboard_router, dependencies=protected)
app.include_router(quant_router, dependencies=protected)
app.include_router(prediction_router, dependencies=protected)
app.include_router(bot_router, dependencies=protected)
app.include_router(orderbook_router, dependencies=protected)
app.include_router(trades_router, dependencies=protected)
app.include_router(paper_router, dependencies=protected)
app.include_router(ws_router)
app.include_router(backtest_router, dependencies=protected)
app.include_router(strategy_router, dependencies=protected)
app.include_router(timeframes_router, dependencies=protected)
app.include_router(ml_router, dependencies=protected)
app.include_router(research_router, dependencies=protected)
app.include_router(stress_router, dependencies=protected)
app.include_router(exchange_router, dependencies=protected)
app.include_router(execution_router, dependencies=protected)
app.include_router(models_router, dependencies=protected)
app.include_router(research_lab_router, dependencies=protected)
app.include_router(logs_router, dependencies=protected)
app.include_router(risk_router, dependencies=protected)
app.include_router(data_router, dependencies=protected)
app.include_router(learning_router, dependencies=protected)
app.include_router(portfolio_router, dependencies=protected)
app.include_router(trading_control_router, dependencies=protected)
app.include_router(bot_trades_router, dependencies=protected)
# admin_config carries its own admin-or-403 dependency (with auditing) on
# every route - the generic `protected` would be redundant on top.
app.include_router(admin_config_router)
app.include_router(health_router)

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "quantx-backend",
        "version": "2.0.0",
        "mode": "paper",
        "time": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/")
async def root():
    return {"message": "QuantX AI Terminal Backend Running"}
