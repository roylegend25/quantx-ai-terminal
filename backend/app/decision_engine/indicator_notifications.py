"""In-app notification feed for indicator-eligibility and settings events
(Bot Settings Part 8). Parallel to app.ml_lab.notifications - not merged
into it, because the dedup semantics differ: MLNotification dedupes
warning-class events within a 6h wall-clock window, whereas this table
dedupes structurally on (event, source_name, symbol, timeframe,
evaluation_version) via a DB unique constraint (insert-or-ignore) - a
genuinely new evaluation version is a new notification regardless of
elapsed time, and a repeat of the same version is always the same event,
never re-posted.

The frontend NotificationBell merges this feed with /api/ml/notifications
rather than adding a second bell.
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import IndicatorNotification
from app.db.session import SessionLocal

EVENT_MOVED_TO_SHADOW_ONLY = "moved_to_shadow_only"
EVENT_RECOMMENDED_FOR_REACTIVATION = "recommended_for_reactivation"
EVENT_SHADOW_PERFORMANCE_DETERIORATED = "shadow_performance_deteriorated"
EVENT_INSUFFICIENT_DATA_QUALITY = "insufficient_data_quality"
EVENT_CONFIG_THRESHOLD_CHANGED = "config_threshold_changed"
EVENT_SETTINGS_COPIED = "settings_copied"

ALL_EVENTS = [
    EVENT_MOVED_TO_SHADOW_ONLY,
    EVENT_RECOMMENDED_FOR_REACTIVATION,
    EVENT_SHADOW_PERFORMANCE_DETERIORATED,
    EVENT_INSUFFICIENT_DATA_QUALITY,
    EVENT_CONFIG_THRESHOLD_CHANGED,
    EVENT_SETTINGS_COPIED,
]

SEVERITIES = ("info", "success", "warning", "error")


def _row_to_dict(row: IndicatorNotification) -> dict:
    return {
        "id": row.id, "event": row.event, "severity": row.severity, "title": row.title,
        "message": row.message, "source_name": row.source_name, "symbol": row.symbol,
        "timeframe": row.timeframe, "evaluation_version": row.evaluation_version,
        "data": row.data, "read": bool(row.read),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def notify(event: str, title: str, message: str | None = None, severity: str = "info",
           source_name: str | None = None, symbol: str | None = None, timeframe: str | None = None,
           evaluation_version: int | None = None, data: dict | None = None,
           db: Session | None = None) -> dict | None:
    """Insert-or-ignore on (event, source_name, symbol, timeframe,
    evaluation_version). Returns the stored row, or None when this exact
    tuple was already posted (structural dedup, not time-based)."""
    if event not in ALL_EVENTS:
        raise ValueError(f"Unknown notification event '{event}'. Valid: {ALL_EVENTS}")
    if severity not in SEVERITIES:
        raise ValueError(f"Unknown severity '{severity}'. Valid: {list(SEVERITIES)}")

    owns_session = db is None
    db = db or SessionLocal()
    try:
        row = IndicatorNotification(
            event=event, severity=severity, title=title, message=message, source_name=source_name,
            symbol=symbol, timeframe=timeframe, evaluation_version=evaluation_version, data=data,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        db.refresh(row)
        return _row_to_dict(row)
    finally:
        if owns_session:
            db.close()


def notify_moved_to_shadow_only(*, source_name: str, symbol: str, timeframe: str, evaluation_version: int,
                                 trigger_snapshot: dict, db: Session | None = None) -> dict | None:
    wrong = trigger_snapshot.get("wrong", "?")
    sample = trigger_snapshot.get("sample_size", "?")
    return notify(
        EVENT_MOVED_TO_SHADOW_ONLY,
        title=f"{source_name} moved to shadow-only on {symbol} {timeframe}",
        message=f"{wrong} of the latest {sample} resolved predictions were wrong - removed from active "
                f"decisions for {symbol} {timeframe} only, continues in shadow.",
        severity="warning", source_name=source_name, symbol=symbol, timeframe=timeframe,
        evaluation_version=evaluation_version, data=trigger_snapshot, db=db,
    )


def notify_recommended_for_reactivation(*, source_name: str, symbol: str, timeframe: str, evaluation_version: int,
                                         stats: dict, db: Session | None = None) -> dict | None:
    correct, wrong, hit_rate = stats.get("correct", "?"), stats.get("wrong", "?"), stats.get("hit_rate")
    hit_rate_pct = f"{hit_rate * 100:.0f}%" if isinstance(hit_rate, (int, float)) else "?"
    return notify(
        EVENT_RECOMMENDED_FOR_REACTIVATION,
        title=f"⭐ {source_name} has performed well in {symbol} {timeframe} shadow testing",
        message=f"{correct} correct, {wrong} wrong, {hit_rate_pct} directional hit rate, positive net "
                f"expectancy. Review and manually enable it.",
        severity="success", source_name=source_name, symbol=symbol, timeframe=timeframe,
        evaluation_version=evaluation_version, data=stats, db=db,
    )


def notify_shadow_performance_deteriorated(*, source_name: str, symbol: str, timeframe: str, evaluation_version: int,
                                            stats: dict, db: Session | None = None) -> dict | None:
    return notify(
        EVENT_SHADOW_PERFORMANCE_DETERIORATED,
        title=f"{source_name} shadow performance deteriorated on {symbol} {timeframe}",
        message="Recommended-for-reactivation status has been withdrawn; the indicator remains shadow-only.",
        severity="warning", source_name=source_name, symbol=symbol, timeframe=timeframe,
        evaluation_version=evaluation_version, data=stats, db=db,
    )


def notify_insufficient_data_quality(*, source_name: str, symbol: str, timeframe: str, evaluation_version: int,
                                      detail: dict, db: Session | None = None) -> dict | None:
    return notify(
        EVENT_INSUFFICIENT_DATA_QUALITY,
        title=f"{source_name} data quality insufficient on {symbol} {timeframe}",
        message="Too many void/data-gap outcomes in the recent window to evaluate reliably.",
        severity="error", source_name=source_name, symbol=symbol, timeframe=timeframe,
        evaluation_version=evaluation_version, data=detail, db=db,
    )


def notify_config_threshold_changed(*, scope: str, fields: list, db: Session | None = None) -> dict | None:
    import time
    return notify(
        EVENT_CONFIG_THRESHOLD_CHANGED,
        title=f"{scope} configuration threshold changed",
        message=f"Fields lowered: {', '.join(fields)}",
        severity="warning", symbol=scope, evaluation_version=int(time.time()),
        data={"scope": scope, "fields": fields}, db=db,
    )


def notify_settings_copied(*, from_scope: str, to_scope: str, db: Session | None = None) -> dict | None:
    import time
    return notify(
        EVENT_SETTINGS_COPIED,
        title=f"Settings copied from {from_scope} to {to_scope}",
        message="This never enables live execution.",
        severity="info", symbol=to_scope, evaluation_version=int(time.time()),
        data={"from_scope": from_scope, "to_scope": to_scope}, db=db,
    )


def list_notifications(limit: int = 50, unread_only: bool = False, db: Session | None = None) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        query = db.query(IndicatorNotification)
        if unread_only:
            query = query.filter(IndicatorNotification.read.is_(False))
        rows = query.order_by(IndicatorNotification.created_at.desc(), IndicatorNotification.id.desc()).limit(limit).all()
        unread = db.query(IndicatorNotification).filter(IndicatorNotification.read.is_(False)).count()
        return {"notifications": [_row_to_dict(r) for r in rows], "unread": unread}
    finally:
        if owns_session:
            db.close()


def mark_read(notification_id: int, db: Session | None = None) -> dict | None:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        row = db.get(IndicatorNotification, notification_id)
        if row is None:
            return None
        row.read = True
        db.commit()
        db.refresh(row)
        return _row_to_dict(row)
    finally:
        if owns_session:
            db.close()


def mark_all_read(db: Session | None = None) -> int:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        updated = (
            db.query(IndicatorNotification)
            .filter(IndicatorNotification.read.is_(False))
            .update({IndicatorNotification.read: True}, synchronize_session=False)
        )
        db.commit()
        return int(updated)
    finally:
        if owns_session:
            db.close()
