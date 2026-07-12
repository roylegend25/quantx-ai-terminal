"""Pre-flight risk gate for REAL (testnet/live) Binance orders (Phase 22).

Runs before any order leaves the process. Every check that fails BLOCKS the
order and the reason is audited - there is no override parameter, and the
gate is enforced inside the execution router itself so no caller can skip
it. Paper trading keeps its own gate (app/trading/risk_manager.py); real
orders additionally pass through this one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.config import settings
from app.risk import settings_repository
from app.trading import modes, protection

# Same idea as the paper execution engine's dedupe window: an identical
# order signature inside this window is a duplicate, not a new decision.
DUPLICATE_WINDOW_SECONDS = 15.0
_recent_signatures: dict[str, float] = {}

MAX_SPREAD_PCT = 1.0  # mirrors app/trading/risk_manager.MAX_SPREAD_PCT


@dataclass
class GateResult:
    allowed: bool
    reason: str
    checks: list[dict] = field(default_factory=list)


def _is_duplicate(signature: str) -> bool:
    now = time.time()
    for key in [k for k, t in _recent_signatures.items() if now - t > DUPLICATE_WINDOW_SECONDS]:
        del _recent_signatures[key]
    if signature in _recent_signatures:
        return True
    _recent_signatures[signature] = now
    return False


def reset_duplicate_guard() -> None:
    """Test hook + kill-switch recovery: forget recent signatures."""
    _recent_signatures.clear()


async def evaluate_real_order(
    symbol: str,
    side: str,
    notional_usdt: float,
    leverage: float,
    client,
    confidence: float | None = None,
    data_reliable: bool | None = None,
    spread_pct: float | None = None,
    open_positions: int | None = None,
    sl: float | None = None,
    tp: float | None = None,
    db=None,
) -> GateResult:
    """Run the full real-order checklist. `client` is a configured
    BinanceFuturesClient (or a test double with the same read methods);
    network-touching checks run last so cheap local rules veto first.

    confidence/data_reliable/spread follow the same fail-open-on-unknown
    policy as the paper gate: None means the caller had no measurement, only
    an explicit bad value vetoes. Everything about money and mode is
    fail-closed."""
    checks: list[dict] = []

    def blocked(name: str, reason: str) -> GateResult:
        checks.append({"check": name, "passed": False, "reason": reason})
        modes.audit(
            "order_blocked_by_risk_gate",
            symbol=symbol,
            detail={"check": name, "reason": reason, "side": side, "notional": notional_usdt},
            db=db,
        )
        return GateResult(allowed=False, reason=reason, checks=checks)

    def passed(name: str) -> None:
        checks.append({"check": name, "passed": True})

    symbol = symbol.upper()
    side = side.upper()

    # ---- mode / lock / kill switch (fail-closed) ----
    if modes.kill_switch_active(db):
        return blocked("kill_switch", "Kill switch active - all trading halted")
    passed("kill_switch")

    mode = modes.effective_mode(db)
    if mode == modes.MODE_LIVE_LOCKED:
        return blocked("live_lock", "Live trading locked - unlock ceremony not completed or disabled by server")
    if mode not in modes.REAL_MODES:
        return blocked("mode", f"Real orders are not allowed in {mode} mode")
    passed("mode")

    if mode == modes.MODE_LIVE and not settings.binance_live_enabled:
        return blocked("live_enabled", "Live trading is disabled by server configuration")
    passed("live_enabled")

    # ---- static limits ----
    if symbol not in settings.binance_allowed_symbols:
        return blocked("symbol_allowed", f"{symbol} is not in BINANCE_ALLOWED_SYMBOLS")
    passed("symbol_allowed")

    if leverage > settings.binance_max_leverage:
        return blocked(
            "max_leverage",
            f"Leverage {leverage:g}x exceeds configured max {settings.binance_max_leverage:g}x",
        )
    passed("max_leverage")

    # Phase 27: an entry that can't be protected must never be placed. This
    # is a hard, local, fail-closed check - it runs before any exchange
    # call and cannot be skipped by a caller omitting sl/tp.
    if settings.binance_require_tp_sl and (sl is None or tp is None):
        return blocked("tp_sl_required", "Live entry requires TP and SL")
    if sl is not None and tp is not None:
        # A cheap, price-free sanity check that SL/TP aren't simply swapped
        # for the side - execution_router additionally runs the full
        # margin.validate_risk_levels() against the real entry/mark price.
        if side == "LONG" and sl >= tp:
            return blocked("tp_sl_required", "Invalid TP/SL for LONG: stop loss must be below take profit")
        if side == "SHORT" and sl <= tp:
            return blocked("tp_sl_required", "Invalid TP/SL for SHORT: stop loss must be above take profit")
    passed("tp_sl_required")

    if notional_usdt <= 0:
        return blocked("notional", "Order notional must be positive")
    if notional_usdt > settings.binance_max_notional_per_trade:
        return blocked(
            "max_notional",
            f"Notional ${notional_usdt:.2f} exceeds configured max "
            f"${settings.binance_max_notional_per_trade:.2f} per trade",
        )
    passed("max_notional")

    risk = settings_repository.get_settings(db=db)

    if open_positions is not None and open_positions >= risk["max_open_positions"]:
        return blocked("max_open_positions", f"Maximum open positions reached ({risk['max_open_positions']})")
    passed("max_open_positions")

    required_confidence = round(risk["min_confidence_to_trade"] * 100, 1)
    if confidence is not None and confidence < required_confidence:
        return blocked(
            "confidence",
            f"Confidence {confidence:.1f}% below required {required_confidence:.1f}%",
        )
    passed("confidence")

    if data_reliable is False:
        return blocked("data_quality", "Market data quality below the usable threshold")
    passed("data_quality")

    if spread_pct is not None and spread_pct >= MAX_SPREAD_PCT:
        return blocked("spread", f"Bid/ask spread {spread_pct:.2f}% exceeds the {MAX_SPREAD_PCT}% safety limit")
    passed("spread")

    # ---- duplicate protection ----
    signature = f"{mode}:{symbol}:{side}:{round(notional_usdt, 2)}"
    if _is_duplicate(signature):
        return blocked("duplicate", "Duplicate order rejected (same order submitted within dedupe window)")
    passed("duplicate")

    # ---- existing unprotected positions block new entries (Phase 27) ----
    if settings.binance_block_new_trades_if_unprotected:
        try:
            live_positions = await client.get_positions()
            live_orders = await client.get_open_orders()
        except Exception as e:
            return blocked("existing_position_protected", f"Could not verify existing position protection: {type(e).__name__}")
        for pos in live_positions:
            result = protection.derive_protection(pos.symbol, live_orders)
            if protection.is_unprotected(result.status):
                return blocked(
                    "existing_position_protected",
                    f"Existing live position is unprotected ({pos.symbol} {result.status})",
                )
    passed("existing_position_protected")

    # ---- exchange-touching checks last ----
    try:
        await client.ping()
    except Exception as e:
        return blocked("exchange_reachable", f"Exchange unreachable: {type(e).__name__}")
    passed("exchange_reachable")

    try:
        daily_pnl = await client.get_daily_realized_pnl()
    except Exception as e:
        return blocked("daily_loss", f"Could not verify daily loss: {type(e).__name__}")
    if daily_pnl < 0 and -daily_pnl >= settings.binance_max_daily_loss_usdt:
        return blocked(
            "daily_loss",
            f"Daily loss limit reached (${-daily_pnl:.2f} >= ${settings.binance_max_daily_loss_usdt:.2f})",
        )
    passed("daily_loss")

    try:
        balances = await client.get_balances()
    except Exception as e:
        return blocked("balance", f"Could not verify balance: {type(e).__name__}")
    usdt_available = next((b.available for b in balances if b.asset == "USDT"), 0.0)
    required_margin = notional_usdt / max(leverage, 1.0)
    if usdt_available < required_margin:
        return blocked(
            "balance",
            f"Insufficient balance: ${usdt_available:.2f} available, "
            f"${required_margin:.2f} margin required",
        )
    passed("balance")

    return GateResult(allowed=True, reason="All real-trading risk checks passed", checks=checks)
