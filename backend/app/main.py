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
from datetime import datetime, timezone
from app.core.deps import get_current_user
from app.db.init_db import init_db
from app.trading.scheduler import start_scheduler
from app.trading.position_manager import start_position_manager

app = FastAPI(title="QuantX AI Terminal API", version="2.0.0")

async def delayed_background_start():
    await asyncio.sleep(5)
    start_scheduler()
    start_position_manager()

@app.on_event("startup")
async def startup_event():
    init_db()
    asyncio.create_task(delayed_background_start())

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
