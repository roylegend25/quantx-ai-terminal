from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.backtest.indicators import add_indicators
from app.db.session import get_db
from app.research import benchmark, monte_carlo, walk_forward
from app.research.experiment_tracker import tracker as experiment_tracker
from app.research.metrics import compute_metrics

router = APIRouter(prefix="/api/research", tags=["research"])


def _history_path(symbol: str, interval: str) -> Path:
    return Path(f"/app/data/history/{symbol.upper()}/{interval}.csv")


@router.get("/experiments")
async def experiments(db: Session = Depends(get_db)):
    return {"experiments": experiment_tracker.list_all(db=db)}


@router.get("/metrics")
async def metrics(symbol: str = "BTCUSDT", interval: str = "5m", db: Session = Depends(get_db)):
    csv_file = _history_path(symbol, interval)
    if not csv_file.exists():
        return {"ok": False, "error": f"{csv_file} not found - call /api/backtest/download first"}

    df = add_indicators(pd.read_csv(csv_file))
    trades = walk_forward.simulate_ensemble(df)
    result_metrics = compute_metrics(trades)

    experiment = experiment_tracker.record(
        metrics=result_metrics,
        notes=f"Adaptive ensemble metrics snapshot for {symbol.upper()} {interval}",
        db=db,
    )

    return {
        "ok": True,
        "symbol": symbol.upper(),
        "interval": interval,
        "metrics": result_metrics,
        "experiment_id": experiment["experiment_id"],
    }


@router.get("/benchmark")
async def benchmark_endpoint(symbol: str = "BTCUSDT", interval: str = "5m", db: Session = Depends(get_db)):
    try:
        result = benchmark.run(symbol=symbol, interval=interval, db=db)
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": True, **result}


@router.get("/montecarlo")
async def montecarlo(
    symbol: str = "BTCUSDT",
    interval: str = "5m",
    simulations: int = 1000,
    db: Session = Depends(get_db),
):
    csv_file = _history_path(symbol, interval)
    if not csv_file.exists():
        return {"ok": False, "error": f"{csv_file} not found - call /api/backtest/download first"}

    df = add_indicators(pd.read_csv(csv_file))
    trades = walk_forward.simulate_ensemble(df)

    try:
        result = monte_carlo.run(trades, simulations=simulations)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "symbol": symbol.upper(), "interval": interval, **result}
