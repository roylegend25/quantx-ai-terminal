from fastapi import APIRouter, Depends, HTTPException
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.backtest.engine import BacktestEngine
from app.backtest.data_loader import save_history
from app.db.session import get_db
from app.research import advanced_backtest

router = APIRouter(prefix="/api/backtest", tags=["Backtest"])


class AdvancedRunRequest(BaseModel):
    """Everything optional beyond strategy: presets fill in defaults, and any
    explicitly provided field overrides its preset value."""
    strategy: str = "ensemble"
    symbols: list[str] | None = None
    timeframes: list[str] | None = None
    preset: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    starting_balance: float | None = None
    train_split: float | None = None
    monte_carlo_sims: int | None = None
    # cost model
    commission_pct: float | None = None
    slippage_bps: float | None = None
    spread_bps: float | None = None
    funding_rate_pct: float | None = None
    latency_ms: float | None = None
    partial_fill_ratio: float | None = None
    # risk model
    atr_sl_mult: float | None = None
    atr_tp_mult: float | None = None
    entry_confidence_threshold: float | None = None
    position_size_usd: float | None = None
    trailing_stop_atr_mult: float | None = None
    max_holding_bars: int | None = None
    daily_loss_limit_pct: float | None = None
    max_drawdown_stop_pct: float | None = None


@router.get("/presets")
async def presets():
    return {"presets": advanced_backtest.PRESETS}


@router.post("/run-advanced")
def run_advanced(req: AdvancedRunRequest, db: Session = Depends(get_db)):
    try:
        result = advanced_backtest.run_advanced(req.model_dump(exclude_none=True), db=db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except advanced_backtest.DataNotAvailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "run": result}


@router.get("/results")
def list_results(limit: int = 30, db: Session = Depends(get_db)):
    return {"runs": advanced_backtest.list_runs(limit=limit, db=db)}


@router.get("/results/{run_id}")
def get_result(run_id: str, db: Session = Depends(get_db)):
    result = advanced_backtest.get_run(run_id, db=db)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return {"ok": True, "run": result}


@router.get("/compare")
def compare(
    symbol: str = "BTCUSDT",
    timeframe: str = "5m",
    start_date: str | None = None,
    end_date: str | None = None,
    db: Session = Depends(get_db),
):
    try:
        return advanced_backtest.compare_strategies(symbol, timeframe, start_date, end_date, db=db)
    except advanced_backtest.DataNotAvailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/walkforward/{run_id}")
def walkforward(run_id: str, train_bars: int = 500, validate_bars: int = 150, db: Session = Depends(get_db)):
    try:
        return {"ok": True, **advanced_backtest.run_walkforward_for(run_id, train_bars, validate_bars, db=db)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except advanced_backtest.DataNotAvailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/montecarlo/{run_id}")
def montecarlo(run_id: str, simulations: int = 1000, db: Session = Depends(get_db)):
    try:
        return {"ok": True, **advanced_backtest.run_montecarlo_for(run_id, simulations, db=db)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

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
