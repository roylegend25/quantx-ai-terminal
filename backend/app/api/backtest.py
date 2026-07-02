from fastapi import APIRouter
from pathlib import Path

from app.backtest.engine import BacktestEngine
from app.backtest.data_loader import save_history

router = APIRouter(prefix="/api/backtest", tags=["Backtest"])

@router.get("/run")
async def run_backtest(
    symbol: str = "BTCUSDT",
    interval: str = "5m",
):
    csv_file = f"/app/data/history/{symbol}/{interval}.csv"

    if not Path(csv_file).exists():
        return {
            "ok": False,
            "error": f"{csv_file} not found"
        }

    engine = BacktestEngine()
    result = engine.run(csv_file)

    return {
        "ok": True,
        "symbol": symbol,
        "interval": interval,
        "results": result,
    }


@router.get("/download")
async def download_history(
    symbol: str = "BTCUSDT",
    interval: str = "5m",
    limit: int = 1000,
):
    result = await save_history(symbol, interval, limit)
    return {
        "ok": True,
        "result": result,
    }
