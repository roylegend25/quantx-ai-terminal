"""Live accuracy tracking + inference monitor, computed entirely from
stored PredictionFeature rows - the same records the prediction chart's
history overlay reads, so numbers here always agree with what's on screen.

A prediction is scored against reality the same way the chart does it:
its target/entry vs the next prediction's observed entry price in the same
(symbol, timeframe) series, or the actual trade exit when a paper trade
resolved it. Nothing is interpolated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy.orm import Session

from app.db.models import PredictionFeature
from app.db.session import SessionLocal

TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


def _scored_rows(symbol: str | None, timeframe: str | None, db: Session, limit: int = 2000) -> list[dict]:
    """Chronological directional predictions with a realized next price.
    Returns dicts: direction, confidence, predicted_price, entry, resolved,
    hit (bool), error_pct, return_if_traded."""
    query = db.query(PredictionFeature).order_by(PredictionFeature.timestamp.asc(), PredictionFeature.id.asc())
    if symbol:
        query = query.filter(PredictionFeature.symbol == symbol)
    if timeframe:
        query = query.filter(PredictionFeature.timeframe == timeframe)
    rows = query.limit(limit).all()

    out: list[dict] = []
    prev_by_series: dict[tuple, PredictionFeature] = {}
    for row in rows:
        key = (row.symbol, row.timeframe)
        prev = prev_by_series.get(key)
        if (
            prev is not None
            and prev.direction in ("LONG", "SHORT")
            and prev.entry_price
        ):
            resolved = prev.exit_price if prev.exit_price else row.entry_price
            if resolved:
                moved_up = resolved > prev.entry_price
                hit = moved_up == (prev.direction == "LONG")
                predicted = prev.target if prev.target and prev.target != prev.entry_price else None
                signed = (resolved - prev.entry_price) / prev.entry_price
                out.append(
                    {
                        "timeframe": prev.timeframe,
                        "symbol": prev.symbol,
                        "direction": prev.direction,
                        "confidence": prev.confidence,
                        "hit": hit,
                        "predicted_price": predicted,
                        "entry_price": prev.entry_price,
                        "resolved_price": resolved,
                        "error_pct": abs(resolved - predicted) / resolved * 100 if predicted else None,
                        "return_if_traded": signed if prev.direction == "LONG" else -signed,
                    }
                )
        prev_by_series[key] = row
    return out


def _timeframe_metrics(scored: list[dict]) -> dict:
    n = len(scored)
    if n == 0:
        return {"predictions": 0, "reason": "No resolved directional predictions on this timeframe yet"}

    hits = [s for s in scored if s["hit"]]
    errors = [s["error_pct"] for s in scored if s["error_pct"] is not None]
    price_pairs = [(s["predicted_price"], s["resolved_price"]) for s in scored if s["predicted_price"]]
    returns = [s["return_if_traded"] for s in scored]

    mae = rmse = mape = None
    if price_pairs:
        diffs = np.array([p - a for p, a in price_pairs], dtype=float)
        actuals = np.array([a for _, a in price_pairs], dtype=float)
        mae = round(float(np.mean(np.abs(diffs))), 4)
        rmse = round(float(np.sqrt(np.mean(diffs**2))), 4)
        mape = round(float(np.mean(np.abs(diffs / actuals))) * 100, 4)

    # calibration error: |avg stated confidence - realized hit rate|
    confidences = [s["confidence"] for s in scored if s["confidence"] is not None]
    hit_rate = len(hits) / n
    calibration_error = (
        round(abs(float(np.mean(confidences)) / 100.0 - hit_rate), 4) if confidences else None
    )

    return {
        "predictions": n,
        "directional_accuracy_pct": round(hit_rate * 100, 2),
        "accuracy_pct": round(hit_rate * 100, 2),
        "mae": mae,
        "rmse": rmse,
        "mape_pct": mape,
        "avg_error_pct": round(float(np.mean(errors)), 3) if errors else None,
        "calibration_error": calibration_error,
        "profit_if_traded_pct": round(float(np.sum(returns)) * 100, 3),
        "avg_confidence": round(float(np.mean(confidences)), 1) if confidences else None,
    }


def live_accuracy(symbol: str | None = None, db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        scored = _scored_rows(symbol, None, db)
        by_tf = {}
        for tf in TIMEFRAMES:
            by_tf[tf] = _timeframe_metrics([s for s in scored if s["timeframe"] == tf])
        return {
            "symbol": symbol,
            "overall": _timeframe_metrics(scored),
            "timeframes": by_tf,
        }
    finally:
        if owns_session:
            db.close()


def inference_monitor(symbol: str | None = None, limit: int = 50, db: Session | None = None) -> dict:
    """Most recent predictions with latency, confidence, signal, resolution
    state, and the running directional accuracy over the returned window."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        query = db.query(PredictionFeature).order_by(PredictionFeature.timestamp.desc(), PredictionFeature.id.desc())
        if symbol:
            query = query.filter(PredictionFeature.symbol == symbol)
        recent = list(reversed(query.limit(limit + 1).all()))

        # resolve each against the NEXT row in its own series (same rule as
        # everywhere else); the newest row per series stays pending
        next_entry: dict[tuple, list[PredictionFeature]] = {}
        for row in recent:
            next_entry.setdefault((row.symbol, row.timeframe), []).append(row)

        items = []
        running_hits = 0
        running_scored = 0
        for row in recent[:-1] if len(recent) > limit else recent:
            series = next_entry[(row.symbol, row.timeframe)]
            idx = series.index(row)
            nxt = series[idx + 1] if idx + 1 < len(series) else None

            outcome = "pending"
            correct = None
            if row.direction not in ("LONG", "SHORT"):
                outcome = "no_trade"
            else:
                resolved = row.exit_price or (nxt.entry_price if nxt else None)
                if resolved and row.entry_price:
                    correct = (resolved > row.entry_price) == (row.direction == "LONG")
                    outcome = "correct" if correct else "incorrect"
                    running_scored += 1
                    running_hits += 1 if correct else 0

            items.append(
                {
                    "id": row.id,
                    "timestamp": row.timestamp.replace(tzinfo=timezone.utc).isoformat() if row.timestamp and row.timestamp.tzinfo is None else (row.timestamp.isoformat() if row.timestamp else None),
                    "symbol": row.symbol,
                    "timeframe": row.timeframe,
                    "signal": row.direction,
                    "confidence": row.confidence,
                    "latency_ms": row.latency_ms,
                    "entry_price": row.entry_price,
                    "outcome": outcome,
                    "correct": correct,
                }
            )

        items.reverse()  # newest first for the monitor table
        return {
            "items": items[:limit],
            "running_accuracy_pct": round(running_hits / running_scored * 100, 2) if running_scored else None,
            "scored": running_scored,
            "pending": sum(1 for i in items if i["outcome"] == "pending"),
        }
    finally:
        if owns_session:
            db.close()


def accuracy_history(days: int = 30, symbol: str | None = None, db: Session | None = None) -> list[dict]:
    """Daily realized directional accuracy + volume of scored predictions,
    for the performance-history charts."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = (
            db.query(PredictionFeature)
            .filter(PredictionFeature.timestamp >= cutoff)
            .order_by(PredictionFeature.timestamp.asc(), PredictionFeature.id.asc())
        )
        if symbol:
            query = query.filter(PredictionFeature.symbol == symbol)
        rows = query.all()

        prev_by_series: dict[tuple, PredictionFeature] = {}
        daily: dict[str, dict] = {}
        for row in rows:
            key = (row.symbol, row.timeframe)
            prev = prev_by_series.get(key)
            if (
                prev is not None
                and prev.direction in ("LONG", "SHORT")
                and prev.entry_price
                and row.entry_price
            ):
                resolved = prev.exit_price or row.entry_price
                hit = (resolved > prev.entry_price) == (prev.direction == "LONG")
                day = prev.timestamp.strftime("%Y-%m-%d")
                bucket = daily.setdefault(day, {"hits": 0, "n": 0, "confidence_sum": 0.0, "conf_n": 0})
                bucket["n"] += 1
                bucket["hits"] += 1 if hit else 0
                if prev.confidence is not None:
                    bucket["confidence_sum"] += prev.confidence
                    bucket["conf_n"] += 1
            prev_by_series[key] = row

        return [
            {
                "date": day,
                "accuracy_pct": round(b["hits"] / b["n"] * 100, 2),
                "scored": b["n"],
                "avg_confidence": round(b["confidence_sum"] / b["conf_n"], 1) if b["conf_n"] else None,
            }
            for day, b in sorted(daily.items())
        ]
    finally:
        if owns_session:
            db.close()
