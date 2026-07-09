"""MLOps notification system.

Every lifecycle event (training started/completed/failed, challenger
created/rejected, champion promoted/rolled back, drift and data-quality
warnings) writes one MLNotification row - that table is what the UI bell
and GET /api/ml/notifications read. External channels (Telegram, Discord,
SMTP email) are best-effort mirrors, attempted only when their env vars are
configured; a channel failure is logged and never propagates, so a dead
webhook can never break training or the API.

Called from the API process, the mlops scheduler, and the training worker
subprocess - all paths only need SessionLocal.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import MLNotification
from app.db.session import SessionLocal
from app.monitoring.logging import get_logger, log_event

logger = get_logger("quantx.ml_notifications")

EVENT_TRAINING_STARTED = "training_started"
EVENT_TRAINING_COMPLETED = "training_completed"
EVENT_TRAINING_FAILED = "training_failed"
EVENT_TRAINING_SKIPPED = "training_skipped"
EVENT_CHALLENGER_CREATED = "challenger_created"
EVENT_CHALLENGER_REJECTED = "challenger_rejected"
EVENT_CHAMPION_PROMOTED = "champion_promoted"
EVENT_CHAMPION_ROLLBACK = "champion_rollback"
EVENT_DRIFT_WARNING = "drift_warning"
EVENT_DATA_QUALITY_WARNING = "data_quality_warning"

ALL_EVENTS = [
    EVENT_TRAINING_STARTED,
    EVENT_TRAINING_COMPLETED,
    EVENT_TRAINING_FAILED,
    EVENT_TRAINING_SKIPPED,
    EVENT_CHALLENGER_CREATED,
    EVENT_CHALLENGER_REJECTED,
    EVENT_CHAMPION_PROMOTED,
    EVENT_CHAMPION_ROLLBACK,
    EVENT_DRIFT_WARNING,
    EVENT_DATA_QUALITY_WARNING,
]

SEVERITIES = ("info", "success", "warning", "error")

# Warning-class events fire from a recurring scheduler cycle; without a
# floor between repeats the bell fills with the same drift warning every
# hour while the condition persists.
DEDUP_WINDOW = timedelta(hours=6)
DEDUPED_EVENTS = {EVENT_DRIFT_WARNING, EVENT_DATA_QUALITY_WARNING}


def _row_to_dict(row: MLNotification) -> dict:
    return {
        "id": row.id,
        "event": row.event,
        "severity": row.severity,
        "title": row.title,
        "message": row.message,
        "data": row.data,
        "read": bool(row.read),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ------------------------------------------------------- external channels


def external_channels() -> list[str]:
    """Which external channels are configured on this deployment."""
    channels = []
    if settings.telegram_bot_token and settings.telegram_chat_id:
        channels.append("telegram")
    if settings.discord_webhook_url:
        channels.append("discord")
    if settings.smtp_host and settings.smtp_from and settings.notify_email_to:
        channels.append("email")
    return channels


def _send_telegram(title: str, message: str | None):
    import httpx

    text = f"*{title}*\n{message or ''}".strip()
    httpx.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
        json={"chat_id": settings.telegram_chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    ).raise_for_status()


def _send_discord(title: str, message: str | None):
    import httpx

    httpx.post(
        settings.discord_webhook_url,
        json={"content": f"**{title}**\n{message or ''}".strip()[:1900]},
        timeout=10,
    ).raise_for_status()


def _send_email(title: str, message: str | None):
    mail = MIMEText(message or title)
    mail["Subject"] = f"[QuantX ML] {title}"
    mail["From"] = settings.smtp_from
    mail["To"] = settings.notify_email_to
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        smtp.ehlo()
        try:
            smtp.starttls()
        except smtplib.SMTPNotSupportedError:
            pass
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(mail)


_SENDERS = {"telegram": _send_telegram, "discord": _send_discord, "email": _send_email}


def _dispatch_external(title: str, message: str | None):
    for channel in external_channels():
        try:
            _SENDERS[channel](title, message)
        except Exception as exc:  # never let a channel failure propagate
            log_event(
                logger, message="ml_notification_channel_error", level=logging.WARNING,
                category="notifications", strategy=channel, error=repr(exc),
            )


# --------------------------------------------------------------- core API


def notify(
    event: str,
    title: str,
    message: str | None = None,
    severity: str = "info",
    data: dict | None = None,
    db: Session | None = None,
) -> dict | None:
    """Records one notification and mirrors it to configured external
    channels. Returns the stored row, or None when a deduped warning was
    suppressed inside its window."""
    if event not in ALL_EVENTS:
        raise ValueError(f"Unknown notification event '{event}'. Valid: {ALL_EVENTS}")
    if severity not in SEVERITIES:
        raise ValueError(f"Unknown severity '{severity}'. Valid: {list(SEVERITIES)}")

    owns_session = db is None
    db = db or SessionLocal()
    try:
        if event in DEDUPED_EVENTS:
            cutoff = datetime.now(timezone.utc) - DEDUP_WINDOW
            recent = (
                db.query(MLNotification)
                .filter(MLNotification.event == event, MLNotification.created_at >= cutoff)
                .first()
            )
            if recent is not None:
                return None

        row = MLNotification(event=event, severity=severity, title=title, message=message, data=data)
        db.add(row)
        db.commit()
        db.refresh(row)
        stored = _row_to_dict(row)
    finally:
        if owns_session:
            db.close()

    _dispatch_external(title, message)
    return stored


def list_notifications(
    limit: int = 50, unread_only: bool = False, db: Session | None = None
) -> dict:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        query = db.query(MLNotification)
        if unread_only:
            query = query.filter(MLNotification.read.is_(False))
        rows = query.order_by(MLNotification.created_at.desc(), MLNotification.id.desc()).limit(limit).all()
        unread = db.query(MLNotification).filter(MLNotification.read.is_(False)).count()
        return {
            "notifications": [_row_to_dict(r) for r in rows],
            "unread": unread,
            "external_channels": external_channels(),
        }
    finally:
        if owns_session:
            db.close()


def mark_read(notification_id: int, db: Session | None = None) -> dict | None:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        row = db.get(MLNotification, notification_id)
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
            db.query(MLNotification)
            .filter(MLNotification.read.is_(False))
            .update({MLNotification.read: True}, synchronize_session=False)
        )
        db.commit()
        return int(updated)
    finally:
        if owns_session:
            db.close()
