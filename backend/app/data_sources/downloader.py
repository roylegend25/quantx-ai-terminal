"""Download orchestration: fetch -> normalize -> validate -> interpolate ->
persist, with a data_download_jobs row tracking every run and a
data_quality_reports row for every candle dataset touched.

Downloads run as asyncio tasks inside the API process (start_download returns
the job id immediately; GET /api/data/jobs polls). All persistence targets
the same SQLite/SQLAlchemy session layer the rest of the app uses.
"""

import asyncio
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.data_sources import (
    binance_futures,
    binance_spot,
    coinglass,
    fear_greed,
    funding as funding_source,
    liquidation as liquidation_source,
    macro,
    open_interest as oi_source,
    orderbook as orderbook_source,
)
from app.data_sources.interpolator import fill_gaps
from app.data_sources.normalizer import (
    SUPPORTED_SYMBOLS,
    TIMEFRAMES_MS,
    normalize_agg_trades,
    normalize_klines,
    timeframe_ms as tf_ms,
)
from app.data_sources.validator import MIN_USABLE_QUALITY, validate_candles
from app.db.models import (
    DataDownloadJob,
    DataQualityReport,
    FundingRateRow,
    LiquidationEstimateRow,
    MarketCandle,
    OpenInterestRow,
    OrderbookSnapshotRow,
    SentimentRow,
    TradeTick,
)
from app.db.session import SessionLocal
from app.monitoring.logging import get_logger

logger = get_logger("quantx.data")

DATA_TYPES = ["candles", "funding", "open_interest", "orderbook", "trades", "sentiment", "liquidations"]
CANDLE_PROVIDERS = ["binance_futures", "binance_spot"]

_now = lambda: datetime.now(timezone.utc)  # noqa: E731


# ---------------------------------------------------------------- storage

def _insert_missing(db: Session, model, key_fn, existing_keys: set, rows: list[dict], build_fn) -> int:
    stored = 0
    for row in rows:
        if key_fn(row) in existing_keys:
            continue
        db.add(build_fn(row))
        existing_keys.add(key_fn(row))
        stored += 1
    return stored


def store_candles(db: Session, symbol: str, timeframe: str, provider: str, rows: list[dict], quality: float) -> int:
    if not rows:
        return 0
    times = [r["time"] for r in rows]
    existing = {
        t[0]
        for t in db.query(MarketCandle.timestamp)
        .filter(
            MarketCandle.symbol == symbol,
            MarketCandle.timeframe == timeframe,
            MarketCandle.provider == provider,
            MarketCandle.timestamp >= min(times),
            MarketCandle.timestamp <= max(times),
        )
        .all()
    }
    stored = _insert_missing(
        db,
        MarketCandle,
        lambda r: r["time"],
        existing,
        rows,
        lambda r: MarketCandle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=r["time"],
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
            quote_volume=r.get("quote_volume"),
            trades_count=r.get("trades_count"),
            interpolated=bool(r.get("interpolated")),
            provider=provider,
            quality_score=quality,
        ),
    )
    db.commit()
    return stored


def load_candles(
    symbol: str,
    timeframe: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int | None = None,
    provider: str | None = None,
    include_interpolated: bool = True,
    db: Session | None = None,
) -> list[dict]:
    owns = db is None
    db = db or SessionLocal()
    try:
        q = db.query(MarketCandle).filter(
            MarketCandle.symbol == symbol.upper(), MarketCandle.timeframe == timeframe
        )
        if provider:
            q = q.filter(MarketCandle.provider == provider)
        if start_ms is not None:
            q = q.filter(MarketCandle.timestamp >= start_ms)
        if end_ms is not None:
            q = q.filter(MarketCandle.timestamp <= end_ms)
        if not include_interpolated:
            q = q.filter(MarketCandle.interpolated.is_(False))
        q = q.order_by(MarketCandle.timestamp.desc())
        if limit:
            q = q.limit(limit)
        rows = q.all()
        rows.reverse()
        return [
            {
                "time": r.timestamp,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
                "interpolated": bool(r.interpolated),
                "provider": r.provider,
                "quality_score": r.quality_score,
            }
            for r in rows
        ]
    finally:
        if owns:
            db.close()


def candle_coverage(db: Session | None = None) -> list[dict]:
    """What's actually stored, per (symbol, timeframe): row count, span, last
    quality score - drives the UI's data inventory panel."""
    from sqlalchemy import Integer as SAInteger, func
    from sqlalchemy.sql import cast

    owns = db is None
    db = db or SessionLocal()
    try:
        rows = (
            db.query(
                MarketCandle.symbol,
                MarketCandle.timeframe,
                func.count(MarketCandle.id),
                func.min(MarketCandle.timestamp),
                func.max(MarketCandle.timestamp),
                func.sum(cast(MarketCandle.interpolated, SAInteger)),
            )
            .group_by(MarketCandle.symbol, MarketCandle.timeframe)
            .all()
        )
        out = []
        for symbol, timeframe, count, first, last, interpolated in rows:
            report = (
                db.query(DataQualityReport)
                .filter(DataQualityReport.symbol == symbol, DataQualityReport.timeframe == timeframe)
                .order_by(DataQualityReport.created_at.desc())
                .first()
            )
            out.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "rows": count,
                    "first_timestamp": first,
                    "last_timestamp": last,
                    "interpolated_rows": int(interpolated or 0),
                    "quality_score": report.quality_score if report else None,
                    "last_checked": report.created_at.isoformat() if report else None,
                }
            )
        return sorted(out, key=lambda r: (r["symbol"], r["timeframe"]))
    finally:
        if owns:
            db.close()


def _save_quality_report(
    db: Session, symbol: str, timeframe: str, provider: str, report: dict, interpolated: int, gap_actions: list[dict]
) -> None:
    rejected = sum(1 for g in gap_actions if g.get("action") == "rejected")
    db.add(
        DataQualityReport(
            symbol=symbol,
            timeframe=timeframe,
            data_type="candles",
            timestamp=int(time.time() * 1000),
            range_start=report.get("range_start"),
            range_end=report.get("range_end"),
            rows=report["rows"],
            expected_rows=report["expected_rows"],
            missing_candles=report["missing_candles"],
            duplicates=report["duplicates"],
            zero_volume=report["zero_volume"],
            broken_ohlc=report["broken_ohlc"],
            outliers=report["outliers"],
            interpolated=interpolated,
            rejected_gaps=rejected,
            largest_gap_bars=report["largest_gap_bars"],
            stale=report["stale"],
            quality_score=report["quality_score"],
            gaps=gap_actions[:100] or report["gaps"],
            details={"outlier_timestamps": report.get("outlier_timestamps", []), "usable": report["usable"]},
            provider=provider,
        )
    )
    db.commit()


# ---------------------------------------------------------------- handlers

async def _handle_candles(job: dict, db: Session) -> dict:
    symbol, timeframe, provider = job["symbol"], job["timeframe"], job["provider"]
    interval_ms = tf_ms(timeframe)
    fetcher = binance_spot.fetch_klines if provider == "binance_spot" else binance_futures.fetch_klines

    raw = await fetcher(
        symbol,
        timeframe,
        start_ms=job.get("requested_start"),
        end_ms=job.get("requested_end"),
        limit=job.get("limit") or 1000,
    )
    rows = normalize_klines(raw)
    clean, report = validate_candles(rows, interval_ms)
    filled, interpolated_count, gap_actions = fill_gaps(clean, interval_ms)

    if clean:
        report["range_start"], report["range_end"] = clean[0]["time"], clean[-1]["time"]

    stored = 0
    if report["usable"]:
        stored = store_candles(db, symbol, timeframe, provider, filled, report["quality_score"])
    _save_quality_report(db, symbol, timeframe, provider, report, interpolated_count, gap_actions)

    if not report["usable"]:
        raise ValueError(
            f"Dataset quality {report['quality_score']} below usable threshold {MIN_USABLE_QUALITY} - "
            f"nothing persisted ({report['missing_candles']} missing, {report['broken_ohlc']} broken bars)"
        )

    return {
        "rows_fetched": len(rows),
        "rows_stored": stored,
        "rows_interpolated": interpolated_count,
        "rows_rejected": report["broken_ohlc"] + report["duplicates"],
        "quality_score": report["quality_score"],
        "detail": {"gaps": gap_actions[:50], "stale": report["stale"]},
    }


async def _handle_funding(job: dict, db: Session) -> dict:
    symbol, provider = job["symbol"], job["provider"]
    rows = await funding_source.history(
        symbol,
        start_ms=job.get("requested_start"),
        end_ms=job.get("requested_end"),
        limit=job.get("limit") or 1000,
        provider="coinglass" if provider == "coinglass" else "binance_futures",
    )
    times = [r["time"] for r in rows]
    existing = set()
    if times:
        existing = {
            t[0]
            for t in db.query(FundingRateRow.timestamp)
            .filter(
                FundingRateRow.symbol == symbol,
                FundingRateRow.provider == provider,
                FundingRateRow.timestamp >= min(times),
                FundingRateRow.timestamp <= max(times),
            )
            .all()
        }
    stored = _insert_missing(
        db,
        FundingRateRow,
        lambda r: r["time"],
        existing,
        rows,
        lambda r: FundingRateRow(
            symbol=symbol,
            timestamp=r["time"],
            funding_rate=r["funding_rate"],
            mark_price=r.get("mark_price"),
            provider=provider,
            quality_score=100.0,
        ),
    )
    db.commit()
    return {"rows_fetched": len(rows), "rows_stored": stored}


async def _handle_open_interest(job: dict, db: Session) -> dict:
    symbol, timeframe, provider = job["symbol"], job["timeframe"] or "5m", job["provider"]
    rows, period = await oi_source.history(
        symbol,
        timeframe=timeframe,
        limit=job.get("limit") or 500,
        start_ms=job.get("requested_start"),
        end_ms=job.get("requested_end"),
        provider="coinglass" if provider == "coinglass" else "binance_futures",
    )
    times = [r["time"] for r in rows]
    existing = set()
    if times:
        existing = {
            t[0]
            for t in db.query(OpenInterestRow.timestamp)
            .filter(
                OpenInterestRow.symbol == symbol,
                OpenInterestRow.timeframe == period,
                OpenInterestRow.provider == provider,
                OpenInterestRow.timestamp >= min(times),
                OpenInterestRow.timestamp <= max(times),
            )
            .all()
        }
    stored = _insert_missing(
        db,
        OpenInterestRow,
        lambda r: r["time"],
        existing,
        rows,
        lambda r: OpenInterestRow(
            symbol=symbol,
            timeframe=period,
            timestamp=r["time"],
            open_interest=r["open_interest"],
            open_interest_value=r.get("open_interest_value"),
            provider=provider,
            quality_score=100.0,
        ),
    )
    db.commit()
    return {"rows_fetched": len(rows), "rows_stored": stored, "detail": {"period_used": period}}


async def _handle_orderbook(job: dict, db: Session) -> dict:
    snap = await orderbook_source.snapshot(job["symbol"], limit=job.get("limit") or 100)
    db.add(
        OrderbookSnapshotRow(
            symbol=job["symbol"],
            timestamp=snap["time"],
            best_bid=snap["best_bid"],
            best_ask=snap["best_ask"],
            spread_bps=snap["spread_bps"],
            bid_depth_usd=snap["bid_depth_usd"],
            ask_depth_usd=snap["ask_depth_usd"],
            imbalance=snap["imbalance"],
            levels=snap["levels"],
            provider=snap["provider"],
            quality_score=0.0 if snap.get("stale") else 100.0,
        )
    )
    db.commit()
    return {"rows_fetched": 1, "rows_stored": 1}


async def _handle_trades(job: dict, db: Session) -> dict:
    symbol = job["symbol"]
    raw = await binance_futures.fetch_agg_trades(symbol, limit=job.get("limit") or 1000)
    rows = normalize_agg_trades(raw)
    ids = [r["trade_id"] for r in rows]
    existing = set()
    if ids:
        existing = {
            t[0]
            for t in db.query(TradeTick.trade_id)
            .filter(TradeTick.symbol == symbol, TradeTick.trade_id >= min(ids), TradeTick.trade_id <= max(ids))
            .all()
        }
    stored = _insert_missing(
        db,
        TradeTick,
        lambda r: r["trade_id"],
        existing,
        rows,
        lambda r: TradeTick(
            symbol=symbol,
            timestamp=r["time"],
            trade_id=r["trade_id"],
            price=r["price"],
            qty=r["qty"],
            side=r["side"],
            provider="binance_futures",
            quality_score=100.0,
        ),
    )
    db.commit()
    return {"rows_fetched": len(rows), "rows_stored": stored}


async def _handle_sentiment(job: dict, db: Session) -> dict:
    rows = await fear_greed.history(limit=job.get("limit") or 365)
    try:
        rows += await macro.rows()
    except Exception:
        pass  # dominance is optional context - fear/greed alone is still a valid download

    stored = 0
    for r in rows:
        exists = (
            db.query(SentimentRow.id)
            .filter(SentimentRow.metric == r["metric"], SentimentRow.timestamp == r["time"])
            .first()
        )
        if exists:
            continue
        db.add(
            SentimentRow(
                timestamp=r["time"],
                metric=r["metric"],
                value=r["value"],
                label=r.get("label"),
                provider="alternative.me" if r["metric"] == "fear_greed" else "coingecko",
                quality_score=100.0,
            )
        )
        stored += 1
    db.commit()
    return {"rows_fetched": len(rows), "rows_stored": stored}


async def _handle_liquidations(job: dict, db: Session) -> dict:
    symbol, timeframe = job["symbol"], job["timeframe"] or "1h"

    if coinglass.available():
        events = await liquidation_source.coinglass_history(symbol, interval=timeframe, limit=job.get("limit") or 500)
        provider, quality = "coinglass", 100.0
    else:
        candles = load_candles(symbol, timeframe, limit=1000, db=db)
        oi_rows = [
            {"time": r.timestamp, "open_interest": r.open_interest}
            for r in db.query(OpenInterestRow)
            .filter(OpenInterestRow.symbol == symbol)
            .order_by(OpenInterestRow.timestamp.asc())
            .all()
        ]
        if not candles or not oi_rows:
            raise ValueError(
                "No Coinglass key and no stored candles/open-interest to derive estimates from - "
                "download candles and open_interest first"
            )
        events = liquidation_source.estimate_from_candles_and_oi(candles, oi_rows)
        provider, quality = "derived", 60.0  # estimates are honestly second-class

    stored = 0
    for e in events:
        exists = (
            db.query(LiquidationEstimateRow.id)
            .filter(
                LiquidationEstimateRow.symbol == symbol,
                LiquidationEstimateRow.timeframe == timeframe,
                LiquidationEstimateRow.timestamp == e["time"],
                LiquidationEstimateRow.side == e["side"],
                LiquidationEstimateRow.provider == provider,
            )
            .first()
        )
        if exists:
            continue
        db.add(
            LiquidationEstimateRow(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=e["time"],
                side=e["side"],
                price_level=e.get("price_level"),
                strength=e.get("strength"),
                notional_usd=e.get("notional_usd"),
                method=e["method"],
                provider=provider,
                quality_score=quality,
            )
        )
        stored += 1
    db.commit()
    return {"rows_fetched": len(events), "rows_stored": stored, "detail": {"method": events[0]["method"] if events else None}}


_HANDLERS = {
    "candles": _handle_candles,
    "funding": _handle_funding,
    "open_interest": _handle_open_interest,
    "orderbook": _handle_orderbook,
    "trades": _handle_trades,
    "sentiment": _handle_sentiment,
    "liquidations": _handle_liquidations,
}


# ---------------------------------------------------------------- job runner

def _job_to_dict(row: DataDownloadJob) -> dict:
    return {
        "job_id": row.job_id,
        "data_type": row.data_type,
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "provider": row.provider,
        "status": row.status,
        "requested_start": row.requested_start,
        "requested_end": row.requested_end,
        "rows_fetched": row.rows_fetched,
        "rows_stored": row.rows_stored,
        "rows_interpolated": row.rows_interpolated,
        "rows_rejected": row.rows_rejected,
        "quality_score": row.quality_score,
        "error": row.error,
        "detail": row.detail,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


async def execute_job(job_id: str) -> dict:
    db = SessionLocal()
    try:
        row = db.query(DataDownloadJob).filter(DataDownloadJob.job_id == job_id).first()
        if row is None:
            raise ValueError(f"Unknown job {job_id}")
        row.status = "running"
        row.started_at = _now()
        db.commit()

        job = {
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "provider": row.provider or "binance_futures",
            "requested_start": row.requested_start,
            "requested_end": row.requested_end,
            "limit": (row.detail or {}).get("limit"),
        }
        try:
            result = await _HANDLERS[row.data_type](job, db)
            row.status = "succeeded"
            row.rows_fetched = result.get("rows_fetched", 0)
            row.rows_stored = result.get("rows_stored", 0)
            row.rows_interpolated = result.get("rows_interpolated", 0)
            row.rows_rejected = result.get("rows_rejected", 0)
            row.quality_score = result.get("quality_score")
            row.detail = {**(row.detail or {}), **(result.get("detail") or {})}
        except Exception as exc:  # noqa: BLE001 - job must record any failure
            db.rollback()
            row = db.query(DataDownloadJob).filter(DataDownloadJob.job_id == job_id).first()
            row.status = "failed"
            row.error = str(exc)[:2000]
            logger.warning("data download job %s failed: %s", job_id, exc)
        row.finished_at = _now()
        db.commit()
        return _job_to_dict(row)
    finally:
        db.close()


def create_job(
    data_type: str,
    symbol: str | None = None,
    timeframe: str | None = None,
    provider: str = "binance_futures",
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int | None = None,
    db: Session | None = None,
) -> dict:
    if data_type not in _HANDLERS:
        raise ValueError(f"Unknown data_type '{data_type}'. Valid: {DATA_TYPES}")
    if data_type != "sentiment":
        if not symbol:
            raise ValueError("symbol is required for this data_type")
        symbol = symbol.upper()
        if symbol not in SUPPORTED_SYMBOLS:
            raise ValueError(f"Unsupported symbol '{symbol}'. Supported: {SUPPORTED_SYMBOLS}")
    if data_type == "candles" and (not timeframe or timeframe.lower() not in TIMEFRAMES_MS):
        raise ValueError(f"timeframe required for candles; supported: {', '.join(TIMEFRAMES_MS)}")

    owns = db is None
    db = db or SessionLocal()
    try:
        row = DataDownloadJob(
            job_id=uuid.uuid4().hex[:12],
            data_type=data_type,
            symbol=symbol,
            timeframe=timeframe.lower() if timeframe else None,
            provider=provider,
            status="queued",
            requested_start=start_ms,
            requested_end=end_ms,
            detail={"limit": limit} if limit else None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _job_to_dict(row)
    finally:
        if owns:
            db.close()


def start_download(**kwargs) -> dict:
    """Create the job row and kick off execution in the background; returns
    the queued job immediately."""
    job = create_job(**kwargs)
    asyncio.create_task(execute_job(job["job_id"]))
    return job


def list_jobs(limit: int = 50, status: str | None = None, db: Session | None = None) -> list[dict]:
    owns = db is None
    db = db or SessionLocal()
    try:
        q = db.query(DataDownloadJob)
        if status:
            q = q.filter(DataDownloadJob.status == status)
        rows = q.order_by(DataDownloadJob.created_at.desc()).limit(limit).all()
        return [_job_to_dict(r) for r in rows]
    finally:
        if owns:
            db.close()
