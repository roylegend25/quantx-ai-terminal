"""Persisted TP/SL protection-provider capability, per mode (Phase 26 Algo
Order Provider).

One Binance account (testnet or live) is either "classic"-capable (POST
/fapi/v1/order accepts STOP_MARKET/TAKE_PROFIT_MARKET) or requires the
"algo" provider (POST /fapi/v1/algoOrder - see
app.exchanges.binance_algo_provider) after the classic endpoint rejects a
protective order with code -4120 STOP_ORDER_SWITCH_ALGO. This module is the
single place that reads/writes which one applies to a given mode, so
app.trading.protection_provider only ever needs to detect it once - every
order after that reuses the stored answer instead of re-discovering it via
a failed classic attempt.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import BinanceProtectionCapability
from app.db.session import SessionLocal

CLASSIC = "classic"
ALGO = "algo"


def get_provider(mode: str) -> str | None:
    """None means undetected yet - callers should try classic first."""
    db = SessionLocal()
    try:
        row = db.query(BinanceProtectionCapability).filter_by(mode=mode).first()
        return row.provider if row else None
    finally:
        db.close()


def set_provider(mode: str, provider: str, reason: str | None = None) -> None:
    db = SessionLocal()
    try:
        row = db.query(BinanceProtectionCapability).filter_by(mode=mode).first()
        if not row:
            row = BinanceProtectionCapability(mode=mode)
        row.provider = provider
        row.detected_at = datetime.now(timezone.utc)
        row.detection_reason = reason
        db.add(row)
        db.commit()
    finally:
        db.close()


def record_error(mode: str, error: str) -> None:
    """Best-effort note of the most recent failure, kept alongside whatever
    provider is currently recorded - purely informational for diagnostics,
    never itself a reason to change the stored provider."""
    db = SessionLocal()
    try:
        row = db.query(BinanceProtectionCapability).filter_by(mode=mode).first()
        if not row:
            row = BinanceProtectionCapability(mode=mode, provider=None)
        row.last_error = error
        db.add(row)
        db.commit()
    finally:
        db.close()


def snapshot(mode: str) -> dict:
    db = SessionLocal()
    try:
        row = db.query(BinanceProtectionCapability).filter_by(mode=mode).first()
        if not row:
            return {"mode": mode, "provider": None, "detected_at": None, "detection_reason": None, "last_error": None}
        return {
            "mode": mode,
            "provider": row.provider,
            "detected_at": row.detected_at.isoformat() if row.detected_at else None,
            "detection_reason": row.detection_reason,
            "last_error": row.last_error,
        }
    finally:
        db.close()


def reset(mode: str) -> None:
    """Test/support hook: forget the detected provider for a mode so the
    next order re-detects from scratch."""
    db = SessionLocal()
    try:
        db.query(BinanceProtectionCapability).filter_by(mode=mode).delete()
        db.commit()
    finally:
        db.close()
