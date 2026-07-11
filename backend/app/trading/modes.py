"""Trading-mode state machine, live-trading lock, kill switch and audit
trail (Phase 22).

Modes:
    PAPER               - simulated ledger only (default, always available)
    BINANCE_TESTNET     - real orders against testnet.binancefuture.com
    BINANCE_LIVE_LOCKED - live requested but not permitted (computed, never
                          stored): the env master lock BINANCE_LIVE_ENABLED
                          is off and/or the UI unlock ceremony hasn't been
                          completed
    BINANCE_LIVE        - real money. Requires BOTH the env lock open and
                          the explicit typed-confirmation unlock.

State lives in the singleton TradingControl row so it survives restarts and
is readable by every gate (risk manager, execution engine, router) without
new plumbing. Everything here reads fresh from the DB - no cached state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.config import settings
from app.db.models import TradingAuditLog, TradingControl
from app.db.session import SessionLocal
from app.monitoring.logging import get_logger, log_event

logger = get_logger("quantx.trading_modes")

MODE_PAPER = "PAPER"
MODE_TESTNET = "BINANCE_TESTNET"
MODE_LIVE_LOCKED = "BINANCE_LIVE_LOCKED"
MODE_LIVE = "BINANCE_LIVE"

# What may be stored as the *requested* mode (LIVE_LOCKED is derived).
STORABLE_MODES = (MODE_PAPER, MODE_TESTNET, MODE_LIVE)

# Modes whose orders actually reach Binance.
REAL_MODES = (MODE_TESTNET, MODE_LIVE)

_ENV_MODE_MAP = {
    "paper": MODE_PAPER,
    "binance_testnet": MODE_TESTNET,
    "binance_live": MODE_LIVE,
}

# The exact sentence the user must type to unlock live trading.
LIVE_UNLOCK_PHRASE = "I UNDERSTAND LIVE TRADING RISK"

# Every one of these must be explicitly acknowledged (true) to unlock live.
# Phase 23: testnet left the product UI, so the ceremony now acknowledges
# real money + paper testing instead of testnet testing.
LIVE_UNLOCK_ACKNOWLEDGEMENTS = (
    "real_money_understood",
    "withdrawal_permission_disabled",
    "ip_whitelisted",
    "losses_possible_understood",
    "risk_limits_accepted",
    "tested_in_paper_mode",
)

# What POST /api/trading/mode accepts. BINANCE_TESTNET remains a valid
# *internal* mode (set_mode still stores it, the test suite and developer
# tooling use it) but it is not selectable from the product UI/API.
USER_SELECTABLE_MODES = (MODE_PAPER, MODE_LIVE)


def _get_or_create(db) -> TradingControl:
    row = db.get(TradingControl, 1)
    if not row:
        row = TradingControl(id=1, mode=_ENV_MODE_MAP.get(settings.trading_mode.lower(), MODE_PAPER))
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _with_session(fn):
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def get_control(db=None) -> dict:
    def read(session):
        row = _get_or_create(session)
        return {
            "mode": row.mode or MODE_PAPER,
            "live_unlocked": bool(row.live_unlocked),
            "live_unlocked_at": row.live_unlocked_at.isoformat() if row.live_unlocked_at else None,
            "kill_switch_active": bool(row.kill_switch_active),
            "kill_switch_reason": row.kill_switch_reason,
            "kill_switch_at": row.kill_switch_at.isoformat() if row.kill_switch_at else None,
        }

    if db is not None:
        return read(db)
    return _with_session(read)


def binance_configured() -> bool:
    return bool(settings.binance_api_key and settings.binance_api_secret)


def effective_mode(db=None) -> str:
    """The mode trading actually happens in right now. A requested
    BINANCE_LIVE degrades to BINANCE_LIVE_LOCKED unless both the server env
    lock is open and the UI unlock ceremony was completed."""
    control = get_control(db)
    mode = control["mode"]
    if mode == MODE_LIVE and not (settings.binance_live_enabled and control["live_unlocked"]):
        return MODE_LIVE_LOCKED
    if mode not in STORABLE_MODES:
        return MODE_PAPER
    return mode


def kill_switch_active(db=None) -> bool:
    return get_control(db)["kill_switch_active"]


def set_kill_switch(active: bool, reason: str | None = None, db=None) -> dict:
    def write(session):
        row = _get_or_create(session)
        row.kill_switch_active = bool(active)
        row.kill_switch_reason = reason if active else None
        row.kill_switch_at = datetime.now(timezone.utc) if active else None
        session.commit()
        return get_control(session)

    result = write(db) if db is not None else _with_session(write)
    audit(
        "kill_switch_activated" if active else "kill_switch_deactivated",
        detail={"reason": reason},
        db=db,
    )
    return result


def set_mode(mode: str, db=None) -> dict:
    """Store a requested mode. Callers validate feasibility (keys present,
    env lock, ...) - this only refuses unknown values and never stores the
    computed LOCKED pseudo-mode."""
    mode = mode.upper()
    if mode not in STORABLE_MODES:
        raise ValueError(f"Unknown trading mode: {mode}")

    def write(session):
        row = _get_or_create(session)
        row.mode = mode
        if mode != MODE_LIVE:
            # Leaving live always re-arms the lock: coming back requires the
            # full unlock ceremony again.
            row.live_unlocked = False
            row.live_unlocked_at = None
        session.commit()
        return get_control(session)

    result = write(db) if db is not None else _with_session(write)
    audit("trading_mode_changed", detail={"mode": mode}, db=db)
    return result


def unlock_live(db=None) -> dict:
    """Flip the UI half of the live lock. Callers (the API endpoint) must
    have already validated the typed phrase + acknowledgements AND that the
    env lock is open."""
    def write(session):
        row = _get_or_create(session)
        row.mode = MODE_LIVE
        row.live_unlocked = True
        row.live_unlocked_at = datetime.now(timezone.utc)
        session.commit()
        return get_control(session)

    result = write(db) if db is not None else _with_session(write)
    audit("live_trading_unlocked", db=db)
    return result


def can_trade(db=None) -> tuple[bool, str]:
    """Whether a REAL (non-paper) order could be placed right now, with the
    first blocking reason. Paper trading is never gated here."""
    control = get_control(db)
    mode = effective_mode(db)

    if control["kill_switch_active"]:
        return False, "Kill switch active - all trading halted"
    if mode == MODE_PAPER:
        return False, "Paper mode active - real orders are not placed"
    if mode == MODE_LIVE_LOCKED:
        if not settings.binance_live_enabled:
            return False, "Live trading disabled by server configuration"
        return False, "Live trading locked - complete the risk acknowledgement first"
    if not binance_configured():
        return False, "Binance API keys are not configured"
    return True, "Trading enabled"


def status_warnings(permission_check: dict | None = None) -> list[str]:
    """Human-readable safety warnings for the exchange status endpoint."""
    warnings: list[str] = []
    mode = effective_mode()
    if mode == MODE_LIVE:
        warnings.append("LIVE trading is enabled - real funds are at risk")
    if settings.binance_live_enabled and mode != MODE_LIVE:
        warnings.append("Server allows live trading (BINANCE_LIVE_ENABLED=true) - keep it locked unless needed")
    if settings.binance_max_leverage > 5:
        warnings.append(f"Configured max leverage {settings.binance_max_leverage:g}x is high")
    if binance_configured():
        if permission_check is None or not permission_check.get("detectable"):
            warnings.append("Cannot verify API key permissions (withdrawal/IP whitelist unknown) - verify manually")
        else:
            if permission_check.get("withdraw_enabled"):
                warnings.append("API key has WITHDRAWAL permission enabled - disable it immediately")
            if not permission_check.get("ip_restricted", True):
                warnings.append("No IP whitelist detected on the API key - restrict it to this server's IP")
    return warnings


def exchange_safe_status(db=None) -> dict:
    """The safe public status shape (Phase 23) - never keys, secrets,
    signatures or headers, and no testnet wording (testnet is internal
    only). `binance_connected` is injected by the /api/exchange/status
    endpoint, which is the only place willing to pay for a network probe."""
    mode = effective_mode(db)
    allowed, reason = can_trade(db)
    control = get_control(db)
    return {
        "active_mode": mode,
        "paper_available": True,
        "binance_live_available": binance_configured(),
        "binance_configured": binance_configured(),
        "binance_live_enabled_by_server": settings.binance_live_enabled,
        "binance_live_unlocked_by_user": control["live_unlocked"],
        "can_trade_binance_live": allowed and mode == MODE_LIVE,
        "reason": reason,
        "kill_switch_active": control["kill_switch_active"],
        "allowed_symbols": settings.binance_allowed_symbols,
        "max_leverage": settings.binance_max_leverage,
        "max_notional_per_trade": settings.binance_max_notional_per_trade,
        "max_daily_loss_usdt": settings.binance_max_daily_loss_usdt,
        "default_leverage": settings.binance_default_leverage,
    }


def audit(event: str, symbol: str | None = None, detail: dict | None = None, db=None) -> None:
    """Persist one trading lifecycle event and mirror it to the structured
    log stream. Never call with secrets in `detail`."""
    def write(session):
        session.add(
            TradingAuditLog(event=event, mode=effective_mode(session), symbol=symbol, detail=detail)
        )
        session.commit()

    try:
        if db is not None:
            write(db)
        else:
            _with_session(write)
    except Exception as e:
        log_event(logger, message="trading_audit_write_failed", level=logging.ERROR, category="trading", error=repr(e))

    log_event(logger, message=event, category="trading", symbol=symbol, detail=detail)
