"""Persistence for Research Lab backtest runs (Phase 16) - see
app/research/lab_engine.py for how a run is produced. Mirrors the
start/record shape of app/mlops/experiment.py and
app/research/experiment_tracker.py, but stores a full, replayable backtest
(trades + equity/drawdown curves) rather than just summary metrics, since
GET /api/research/lab/montecarlo/{id} and /walkforward/{id} need the
original run's configuration back.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import ResearchLabExperiment
from app.db.session import SessionLocal


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _serialize_trades(trades: list[dict]) -> list[dict]:
    return [{**t, "entry_time": _iso(t.get("entry_time")), "exit_time": _iso(t.get("exit_time"))} for t in trades]


def _row_to_dict(row: ResearchLabExperiment, include_trades: bool = True) -> dict:
    results = row.results or {}
    if not include_trades:
        results = {k: v for k, v in results.items() if k != "trades"}

    return {
        "experiment_id": row.experiment_id,
        "strategy": row.strategy,
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "start_date": row.start_date.isoformat() if row.start_date else None,
        "end_date": row.end_date.isoformat() if row.end_date else None,
        "parameters": row.parameters,
        "fees": row.fees,
        "results": results,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class LabRepository:
    def save(self, *, run: dict, parameters: dict, fees: dict, db: Session | None = None) -> dict:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            results = {
                "metrics": run["metrics"],
                "trades": _serialize_trades(run["trades"]),
                "equity_curve": run["equity_curve"],
                "drawdown_curve": run["drawdown_curve"],
                "bars": run["bars"],
            }
            row = ResearchLabExperiment(
                experiment_id=uuid.uuid4().hex[:12],
                strategy=run["strategy"],
                symbol=run["symbol"],
                timeframe=run["timeframe"],
                start_date=run["start_date"],
                end_date=run["end_date"],
                parameters=parameters,
                fees=fees,
                results=results,
                created_at=datetime.now(timezone.utc),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return _row_to_dict(row)
        finally:
            if owns_session:
                db.close()

    def get(self, experiment_id: str, db: Session | None = None) -> dict | None:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            row = db.query(ResearchLabExperiment).filter(ResearchLabExperiment.experiment_id == experiment_id).first()
            return _row_to_dict(row) if row else None
        finally:
            if owns_session:
                db.close()

    def list_all(
        self,
        strategy: str | None = None,
        symbol: str | None = None,
        limit: int = 200,
        db: Session | None = None,
    ) -> list[dict]:
        """Summary view (no per-trade detail) for GET /api/research/lab/experiments."""
        owns_session = db is None
        db = db or SessionLocal()
        try:
            query = db.query(ResearchLabExperiment)
            if strategy:
                query = query.filter(ResearchLabExperiment.strategy == strategy)
            if symbol:
                query = query.filter(ResearchLabExperiment.symbol == symbol.upper())
            rows = query.order_by(ResearchLabExperiment.created_at.desc()).limit(limit).all()
            return [_row_to_dict(r, include_trades=False) for r in rows]
        finally:
            if owns_session:
                db.close()


repository = LabRepository()
