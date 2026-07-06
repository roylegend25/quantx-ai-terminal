"""Phase 20 data-engine API: provider status, downloads, jobs, quality/gap
reports, stored candles and generated features."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.data_sources import coinglass, downloader, scheduler as data_scheduler
from app.data_sources.normalizer import SUPPORTED_SYMBOLS, TIMEFRAMES_MS
from app.db.models import DataQualityReport
from app.db.session import get_db
from app.features import engine as feature_engine

router = APIRouter(prefix="/api/data", tags=["data"])


class DownloadRequest(BaseModel):
    data_type: str = "candles"
    symbol: str | None = "BTCUSDT"
    timeframe: str | None = "5m"
    provider: str = "binance_futures"
    start_date: str | None = None  # ISO date; converted to epoch ms
    end_date: str | None = None
    limit: int | None = 1000


def _iso_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    import pandas as pd

    return int(pd.to_datetime(value, utc=True).timestamp() * 1000)


@router.get("/sources")
async def sources():
    return {
        "symbols": SUPPORTED_SYMBOLS,
        "timeframes": list(TIMEFRAMES_MS),
        "data_types": downloader.DATA_TYPES,
        "providers": [
            {"name": "binance_futures", "available": True, "kind": "market", "requires_key": False},
            {"name": "binance_spot", "available": True, "kind": "market", "requires_key": False},
            {"name": "alternative.me", "available": True, "kind": "sentiment", "requires_key": False},
            {"name": "coingecko", "available": True, "kind": "macro", "requires_key": False},
            coinglass.status() | {"kind": "derivatives", "requires_key": True, "name": "coinglass"},
        ],
        "scheduler": {
            "enabled": data_scheduler.enabled(),
            "interval_seconds": data_scheduler.interval_seconds(),
            "timeframes": data_scheduler.REFRESH_TIMEFRAMES,
        },
    }


@router.post("/download")
async def download(req: DownloadRequest):
    try:
        job = downloader.start_download(
            data_type=req.data_type,
            symbol=req.symbol,
            timeframe=req.timeframe,
            provider=req.provider,
            start_ms=_iso_to_ms(req.start_date),
            end_ms=_iso_to_ms(req.end_date),
            limit=req.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "job": job}


@router.get("/jobs")
async def jobs(status: str | None = None, limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    return {"jobs": downloader.list_jobs(limit=limit, status=status, db=db)}


@router.get("/quality")
async def quality(
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(DataQualityReport)
    if symbol:
        q = q.filter(DataQualityReport.symbol == symbol.upper())
    if timeframe:
        q = q.filter(DataQualityReport.timeframe == timeframe)
    rows = q.order_by(DataQualityReport.created_at.desc()).limit(limit).all()
    return {
        "reports": [
            {
                "symbol": r.symbol,
                "timeframe": r.timeframe,
                "data_type": r.data_type,
                "provider": r.provider,
                "rows": r.rows,
                "expected_rows": r.expected_rows,
                "missing_candles": r.missing_candles,
                "duplicates": r.duplicates,
                "zero_volume": r.zero_volume,
                "broken_ohlc": r.broken_ohlc,
                "outliers": r.outliers,
                "interpolated": r.interpolated,
                "rejected_gaps": r.rejected_gaps,
                "largest_gap_bars": r.largest_gap_bars,
                "stale": r.stale,
                "quality_score": r.quality_score,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "coverage": downloader.candle_coverage(db=db),
    }


@router.get("/gaps")
async def gaps(
    symbol: str = "BTCUSDT",
    timeframe: str = "5m",
    db: Session = Depends(get_db),
):
    report = (
        db.query(DataQualityReport)
        .filter(DataQualityReport.symbol == symbol.upper(), DataQualityReport.timeframe == timeframe)
        .order_by(DataQualityReport.created_at.desc())
        .first()
    )
    if report is None:
        raise HTTPException(status_code=404, detail=f"No quality report for {symbol} {timeframe} - download data first")
    return {
        "symbol": report.symbol,
        "timeframe": report.timeframe,
        "checked_at": report.created_at.isoformat() if report.created_at else None,
        "missing_candles": report.missing_candles,
        "rejected_gaps": report.rejected_gaps,
        "largest_gap_bars": report.largest_gap_bars,
        "interpolated": report.interpolated,
        "gaps": report.gaps or [],
        "quality_score": report.quality_score,
    }


@router.get("/candles")
async def candles(
    symbol: str = "BTCUSDT",
    timeframe: str = "5m",
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = Query(default=1000, ge=1, le=20000),
    include_interpolated: bool = True,
    db: Session = Depends(get_db),
):
    if timeframe.lower() not in TIMEFRAMES_MS:
        raise HTTPException(status_code=422, detail=f"Unsupported timeframe '{timeframe}'")
    rows = downloader.load_candles(
        symbol,
        timeframe.lower(),
        start_ms=_iso_to_ms(start_date),
        end_ms=_iso_to_ms(end_date),
        limit=limit,
        include_interpolated=include_interpolated,
        db=db,
    )
    return {"symbol": symbol.upper(), "timeframe": timeframe.lower(), "count": len(rows), "candles": rows}


@router.get("/features")
async def features(
    symbol: str = "BTCUSDT",
    timeframe: str = "5m",
    limit: int = Query(default=200, ge=1, le=1000),
    refresh: bool = True,
    db: Session = Depends(get_db),
):
    """Generated multi-timeframe features. With refresh=true (default) they
    are recomputed from the stored candles and persisted before returning."""
    summary = None
    if refresh:
        try:
            summary = feature_engine.generate_and_store(symbol, timeframe.lower(), db=db)
        except feature_engine.InsufficientDataError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    snapshots = feature_engine.load_snapshots(symbol, timeframe.lower(), limit=limit, db=db)
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe.lower(),
        "feature_version": feature_engine.FEATURE_VERSION,
        "summary": summary,
        "count": len(snapshots),
        "snapshots": snapshots,
    }
