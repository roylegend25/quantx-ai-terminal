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
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db.models import LiveAuthorizationLease, TradingAuditLog, TradingControl
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

# Phase 31: a second, distinct typed phrase - deliberately different text
# from LIVE_UNLOCK_PHRASE so it cannot be satisfied by copy-pasting the
# same value twice - required in addition to the password, phrase, and
# acknowledgements before a live-authorization lease is granted.
LIVE_LEASE_SECOND_CONFIRMATION_PHRASE = "CONFIRM LIVE EXECUTION NOW"

# Hard ceiling on the lease TTL regardless of what live_lease_ttl_seconds is
# configured to - so a misconfigured .env cannot turn the lease back into a
# de-facto permanent unlock.
_MAX_LEASE_TTL_SECONDS = 3600


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
    if active:
        # Emergency Stop revokes any live-authorization lease immediately -
        # it must never remain valid for a subsequent order once the switch
        # is thrown, even if the switch is turned back off later.
        revoke_all_leases("kill_switch", db=db)
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
    if mode != MODE_LIVE:
        revoke_all_leases("mode_changed_away_from_live", db=db)
    audit("trading_mode_changed", detail={"mode": mode}, db=db)
    return result


def unlock_live(db=None) -> dict:
    """Flip the UI half of the live lock. Callers (the API endpoint) must
    have already validated the typed phrase + acknowledgements AND that the
    env lock is open.

    Phase 31: this flag is display/backward-compat state only - it is NO
    LONGER sufficient by itself to place a real order. real_risk_gate also
    requires an active, unexpired LiveAuthorizationLease (see
    create_live_lease), which unlike this boolean cannot survive a restart
    and expires on its own even if nothing else ever locks it again."""
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


# ================================================ Phase 31: live-order lease
#
# The actual authorization a real order checks (real_risk_gate.py) is this
# lease, not TradingControl.live_unlocked. It is created only by the
# interactive unlock ceremony (app.api.trading_control.unlock_live), never
# by the scheduler or any automated path, expires quickly, is consumed
# after settings.live_lease_max_actions real orders, and is wiped
# unconditionally by revoke_all_leases on kill-switch, on leaving LIVE mode,
# and on every backend startup (startup_safety_reset below).

def create_live_lease(user: str, symbol_scope: str | None = None, db=None) -> dict:
    """Grants a new short-lived lease. Callers (the API endpoint) must have
    already verified: fresh password re-authentication, the primary typed
    phrase, the second distinct typed phrase, every acknowledgement, the
    account confirmation, and the symbol confirmation."""
    ttl = max(10, min(settings.live_lease_ttl_seconds, _MAX_LEASE_TTL_SECONDS))
    max_actions = max(1, settings.live_lease_max_actions)
    now = datetime.now(timezone.utc)

    def write(session):
        lease = LiveAuthorizationLease(
            user=user, symbol_scope=symbol_scope, created_at=now,
            expires_at=now + timedelta(seconds=ttl),
            actions_remaining=max_actions,
        )
        session.add(lease)
        session.commit()
        session.refresh(lease)
        return _lease_dict(lease)

    result = write(db) if db is not None else _with_session(write)
    audit("live_unlock_approved", detail={"user": user, "symbol_scope": symbol_scope,
                                           "ttl_seconds": ttl, "max_actions": max_actions}, db=db)
    return result


def _lease_dict(lease: LiveAuthorizationLease) -> dict:
    now = datetime.now(timezone.utc)
    expires_at = lease.expires_at.replace(tzinfo=timezone.utc) if lease.expires_at and lease.expires_at.tzinfo is None else lease.expires_at
    seconds_remaining = max(0, int((expires_at - now).total_seconds())) if expires_at else 0
    return {
        "id": lease.id,
        "user": lease.user,
        "symbol_scope": lease.symbol_scope,
        "created_at": lease.created_at.isoformat() if lease.created_at else None,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "seconds_remaining": seconds_remaining,
        "actions_remaining": lease.actions_remaining,
        "revoked": bool(lease.revoked),
        "revoked_reason": lease.revoked_reason,
        "active": (not lease.revoked) and seconds_remaining > 0 and lease.actions_remaining > 0,
    }


def _expire_stale_leases(session) -> None:
    """Mark-on-read: any lease past its TTL that isn't already revoked gets
    revoked here, so the audit trail records live_unlock_expired at the
    moment it's actually observed instead of only inferring it later."""
    now = datetime.now(timezone.utc)
    stale = (
        session.query(LiveAuthorizationLease)
        .filter(LiveAuthorizationLease.revoked.is_(False), LiveAuthorizationLease.expires_at < now)
        .all()
    )
    for lease in stale:
        lease.revoked = True
        lease.revoked_reason = "expired"
        lease.revoked_at = now
    if stale:
        session.commit()
        for lease in stale:
            audit("live_unlock_expired", detail={"user": lease.user, "lease_id": lease.id}, db=session)


def get_active_lease(db=None) -> dict | None:
    """The current active lease, if any - non-revoked, unexpired, with
    actions remaining. Never more than one is meaningfully active at a
    time in practice (creating a new one doesn't revoke an older still-live
    one, but real orders consume the most recently created active lease
    first)."""
    def read(session):
        _expire_stale_leases(session)
        lease = (
            session.query(LiveAuthorizationLease)
            .filter(LiveAuthorizationLease.revoked.is_(False), LiveAuthorizationLease.actions_remaining > 0)
            .order_by(LiveAuthorizationLease.id.desc())
            .first()
        )
        if lease is None:
            return None
        d = _lease_dict(lease)
        return d if d["active"] else None

    return read(db) if db is not None else _with_session(read)


def consume_active_lease(db=None) -> dict | None:
    """Called exactly once after a real order is actually accepted by the
    exchange (never on a blocked/failed attempt). Decrements the active
    lease's action budget; once it hits zero the lease is revoked, so by
    default (live_lease_max_actions=1) a single real order immediately
    re-locks live trading until a human re-authorizes again."""
    def write(session):
        _expire_stale_leases(session)
        lease = (
            session.query(LiveAuthorizationLease)
            .filter(LiveAuthorizationLease.revoked.is_(False), LiveAuthorizationLease.actions_remaining > 0)
            .order_by(LiveAuthorizationLease.id.desc())
            .first()
        )
        if lease is None:
            return None
        lease.actions_remaining -= 1
        now = datetime.now(timezone.utc)
        if lease.actions_remaining <= 0:
            lease.revoked = True
            lease.revoked_reason = "consumed"
            lease.revoked_at = now
            lease.consumed_at = now
        session.commit()
        return _lease_dict(lease)

    result = write(db) if db is not None else _with_session(write)
    if result is not None:
        audit("live_order_lease_consumed", detail={"lease_id": result["id"],
                                                     "actions_remaining": result["actions_remaining"]}, db=db)
        if result["revoked"]:
            audit("live_unlock_revoked", detail={"lease_id": result["id"], "reason": "consumed"}, db=db)
    return result


def revoke_all_leases(reason: str, db=None) -> int:
    """Revokes every currently-active lease. Called by: kill switch
    activation, leaving LIVE mode, and unconditionally on every backend
    startup. Returns the number of leases actually revoked (0 is the
    common case and is not itself worth a log line)."""
    now = datetime.now(timezone.utc)

    def write(session):
        active = (
            session.query(LiveAuthorizationLease)
            .filter(LiveAuthorizationLease.revoked.is_(False))
            .all()
        )
        for lease in active:
            lease.revoked = True
            lease.revoked_reason = reason
            lease.revoked_at = now
        if active:
            session.commit()
        return [lease.id for lease in active]

    revoked_ids = write(db) if db is not None else _with_session(write)
    if revoked_ids:
        audit("live_unlock_revoked", detail={"lease_ids": revoked_ids, "reason": reason}, db=db)
    return len(revoked_ids)


def startup_safety_reset(db=None) -> dict:
    """Phase 31: called once from the FastAPI startup handler, after the DB
    schema is confirmed compatible. Unconditionally forces the runtime back
    to PAPER and wipes every live lease, regardless of what was persisted -
    so a live unlock can never survive a backend restart, redeploy, or
    server reboot. This is deliberately NOT conditional on what mode was
    last stored: the whole point is that persisted LIVE/unlocked state is
    never trusted across a process boundary."""
    def write(session):
        row = _get_or_create(session)
        row.mode = MODE_PAPER
        row.live_unlocked = False
        row.live_unlocked_at = None
        session.commit()
        return get_control(session)

    result = write(db) if db is not None else _with_session(write)
    revoke_all_leases("startup_reset", db=db)
    audit("trading_mode_changed", detail={"mode": MODE_PAPER, "reason": "startup_reset"}, db=db)

    if settings.binance_live_enabled:
        # Not fatal - the app still starts and stays paper-only (mode was
        # just forced above) - but this is loud on purpose: an operator
        # should know BINANCE_LIVE_ENABLED was left on across a restart.
        log_event(logger, message="startup_live_flag_detected", level=logging.WARNING, category="trading",
                  detail={"binance_live_enabled": True})
        audit("startup_live_flag_detected", detail={"binance_live_enabled": True}, db=db)

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


def binance_live_configured() -> bool:
    """Whether a SEPARATE live-write credential pair has been provisioned.
    Deliberately distinct from binance_configured() (the read-only-
    monitoring + testnet pair) - see Settings.binance_live_api_key."""
    return bool(settings.binance_live_api_key and settings.binance_live_api_secret)


def exchange_safe_status(db=None) -> dict:
    """The safe public status shape (Phase 23) - never keys, secrets,
    signatures or headers, and no testnet wording (testnet is internal
    only). `binance_connected` is injected by the /api/exchange/status
    endpoint, which is the only place willing to pay for a network probe."""
    mode = effective_mode(db)
    allowed, reason = can_trade(db)
    control = get_control(db)
    lease = get_active_lease(db)
    # Phase 31: final order-routing eligibility requires every independent
    # gate at once - server lock, DB mode, kill switch off, AND an active
    # lease. None of the individual fields below may be read as equivalent
    # to this on their own.
    final_order_routing_eligible = bool(
        allowed and mode == MODE_LIVE and not control["kill_switch_active"] and lease is not None
    )
    return {
        "active_mode": mode,
        "paper_available": True,
        "binance_live_available": binance_configured(),
        "binance_configured": binance_configured(),
        "binance_live_enabled_by_server": settings.binance_live_enabled,
        "binance_live_credentials_configured": binance_live_configured(),
        "binance_live_unlocked_by_user": control["live_unlocked"],
        "can_trade_binance_live": allowed and mode == MODE_LIVE,
        "live_authorization_lease": lease,
        "final_order_routing_eligible": final_order_routing_eligible,
        "automatic_execution_mode": "PAPER" if mode != MODE_LIVE else "LIVE",
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
