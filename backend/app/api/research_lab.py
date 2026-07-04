from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.research import lab_engine, monte_carlo
from app.research.execution_sim import CostConfig, RiskConfig
from app.research.lab_repository import repository as lab_repository
from app.research.lab_strategies import STRATEGIES, ModelNotAvailableError

router = APIRouter(prefix="/api/research/lab", tags=["research-lab"])


class RunRequest(BaseModel):
    strategy: str
    symbol: str = "BTCUSDT"
    timeframe: str = "5m"
    start_date: str | None = None
    end_date: str | None = None
    starting_balance: float = 10000.0
    position_size_usd: float = 1000.0
    commission_pct: float = 0.04
    slippage_bps: float = 2.0
    spread_bps: float = 2.0
    funding_rate_pct: float = 0.01
    latency_ms: float = 250.0
    partial_fill_ratio: float = 1.0
    atr_sl_mult: float = 1.5
    atr_tp_mult: float = 3.0
    entry_confidence_threshold: float = 50.0
    save: bool = True


def _cost_from(req: RunRequest) -> CostConfig:
    return CostConfig(
        commission_pct=req.commission_pct,
        slippage_bps=req.slippage_bps,
        spread_bps=req.spread_bps,
        funding_rate_pct=req.funding_rate_pct,
        latency_ms=req.latency_ms,
        partial_fill_ratio=req.partial_fill_ratio,
    )


def _risk_from(req: RunRequest) -> RiskConfig:
    return RiskConfig(
        atr_sl_mult=req.atr_sl_mult,
        atr_tp_mult=req.atr_tp_mult,
        entry_confidence_threshold=req.entry_confidence_threshold,
        position_size_usd=req.position_size_usd,
    )


def _parse_iso(value: str | None):
    return datetime.fromisoformat(value) if value else None


@router.get("/experiments")
async def list_experiments(strategy: str | None = None, symbol: str | None = None, db: Session = Depends(get_db)):
    return {"experiments": lab_repository.list_all(strategy=strategy, symbol=symbol, db=db)}


@router.post("/run")
async def run(req: RunRequest, db: Session = Depends(get_db)):
    if req.strategy not in STRATEGIES:
        raise HTTPException(status_code=422, detail=f"Unknown strategy '{req.strategy}', expected one of {STRATEGIES}")

    try:
        result = lab_engine.run_backtest(
            strategy=req.strategy,
            symbol=req.symbol,
            timeframe=req.timeframe,
            start_date=req.start_date,
            end_date=req.end_date,
            cost=_cost_from(req),
            risk=_risk_from(req),
            starting_balance=req.starting_balance,
            db=db,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ModelNotAvailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    experiment = None
    if req.save:
        experiment = lab_repository.save(
            run=result,
            parameters={
                "atr_sl_mult": req.atr_sl_mult,
                "atr_tp_mult": req.atr_tp_mult,
                "entry_confidence_threshold": req.entry_confidence_threshold,
                "position_size_usd": req.position_size_usd,
            },
            fees={
                "commission_pct": req.commission_pct,
                "slippage_bps": req.slippage_bps,
                "spread_bps": req.spread_bps,
                "funding_rate_pct": req.funding_rate_pct,
                "latency_ms": req.latency_ms,
                "partial_fill_ratio": req.partial_fill_ratio,
            },
            db=db,
        )

    return {"ok": True, "run": result, "experiment": experiment}


@router.get("/experiment/{experiment_id}")
async def get_experiment(experiment_id: str, db: Session = Depends(get_db)):
    experiment = lab_repository.get(experiment_id, db=db)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")
    return {"experiment": experiment}


@router.get("/compare")
async def compare(
    symbol: str = "BTCUSDT",
    timeframe: str = "5m",
    start_date: str | None = None,
    end_date: str | None = None,
    starting_balance: float = 10000.0,
    db: Session = Depends(get_db),
):
    results = []
    for strategy in STRATEGIES:
        try:
            run_result = lab_engine.run_backtest(
                strategy=strategy,
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                starting_balance=starting_balance,
                db=db,
            )
            results.append({"strategy": strategy, "ok": True, "metrics": run_result["metrics"]})
        except FileNotFoundError as exc:
            return {"ok": False, "error": str(exc)}
        except ModelNotAvailableError as exc:
            results.append({"strategy": strategy, "ok": False, "error": str(exc)})

    return {"ok": True, "symbol": symbol.upper(), "timeframe": timeframe, "results": results}


@router.get("/montecarlo/{experiment_id}")
async def montecarlo(experiment_id: str, simulations: int = 1000, db: Session = Depends(get_db)):
    experiment = lab_repository.get(experiment_id, db=db)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")

    trades = experiment["results"].get("trades", [])
    starting_balance = experiment["results"].get("metrics", {}).get("starting_balance", 10000.0)
    parsed_trades = [
        {**t, "entry_time": _parse_iso(t.get("entry_time")), "exit_time": _parse_iso(t.get("exit_time"))}
        for t in trades
    ]

    try:
        result = monte_carlo.run(parsed_trades, starting_balance=starting_balance, simulations=simulations)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {"ok": True, "experiment_id": experiment_id, **result}


@router.get("/walkforward/{experiment_id}")
async def walkforward(
    experiment_id: str,
    train_bars: int = 500,
    validate_bars: int = 150,
    db: Session = Depends(get_db),
):
    experiment = lab_repository.get(experiment_id, db=db)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")

    fees = experiment.get("fees") or {}
    params = experiment.get("parameters") or {}
    cost = CostConfig(**{k: v for k, v in fees.items() if k in CostConfig.__dataclass_fields__})
    risk = RiskConfig(**{k: v for k, v in params.items() if k in RiskConfig.__dataclass_fields__})

    try:
        result = lab_engine.run_walkforward(
            strategy=experiment["strategy"],
            symbol=experiment["symbol"],
            timeframe=experiment["timeframe"],
            cost=cost,
            risk=risk,
            train_bars=train_bars,
            validate_bars=validate_bars,
            db=db,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {"ok": True, "experiment_id": experiment_id, **result}
