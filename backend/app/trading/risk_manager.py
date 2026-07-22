from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import settings
from app.risk import settings_repository

@dataclass
class RiskLevels:
    stop_loss: float
    take_profit: float
    trailing_stop: float
    break_even: float

def calculate_levels(entry: float, atr: float, side: str):
    atr = max(float(atr or 1), 1)
    side = side.upper()

    if side == "LONG":
        sl = entry - atr * settings.atr_sl_mult
        tp = entry + atr * settings.atr_tp_mult
    elif side == "SHORT":
        sl = entry + atr * settings.atr_sl_mult
        tp = entry - atr * settings.atr_tp_mult
    else:
        sl = entry
        tp = entry

    return RiskLevels(
        stop_loss=round(sl, 2),
        take_profit=round(tp, 2),
        trailing_stop=round(atr * 1.2, 2),
        break_even=round(atr * 1.0, 2),
    )

def basic_trade_allowed(confidence: float, open_positions: int):
    risk = settings_repository.get_settings(scope="paper")
    required = risk["min_confidence_to_trade"] * 100

    if confidence < required:
        return False, "Confidence below threshold"

    if open_positions >= risk["max_open_positions"]:
        return False, "Maximum open positions reached"

    return True, "Risk checks passed"


# Real execution against a >1%-wide book would fill far worse than the
# modeled entry price, so a spread this wide vetoes a new trade outright
# rather than just discounting confidence.
MAX_SPREAD_PCT = 1.0

# A candle trading below 5% of its own 20-bar average volume reads as a
# halted or fully illiquid market - a "confident" signal there is really
# just noise with nobody on the other side of the trade.
MIN_VOLUME_RATIO = 0.05


def spread_allowed(spread_pct: float | None) -> tuple[bool, str | None]:
    """spread_pct is unknown (None) whenever the caller has no live order
    book to hand (e.g. the lightweight per-request prediction gate, which
    only ever sees candles) - fail open there rather than blocking on data
    that was never available, and only veto when a spread was actually
    measured and is unsafe."""
    if spread_pct is None:
        return True, None
    if spread_pct >= MAX_SPREAD_PCT:
        return False, f"Bid/ask spread {spread_pct:.2f}% exceeds the {MAX_SPREAD_PCT}% safety limit"
    return True, None


def volume_allowed(volume: float | None, volume_sma20: float | None) -> tuple[bool, str | None]:
    """Same fail-open-on-unknown-data policy as spread_allowed() - only
    vetoes when the latest candle's volume is actually known and clearly
    indicates a halted/illiquid market."""
    if volume is None:
        return True, None
    if volume <= 0:
        return False, "Zero trading volume on the latest candle (halted/illiquid market)"
    if volume_sma20 and volume_sma20 > 0:
        ratio = volume / volume_sma20
        if ratio < MIN_VOLUME_RATIO:
            return False, f"Volume is {ratio * 100:.1f}% of its 20-bar average (halted/illiquid market)"
    return True, None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _consecutive_losses(trade_history: list[dict]) -> int:
    """trade_history is assumed newest-first (as returned by
    GET /api/paper/history). Counts trailing closed losses before the most
    recent win (or the list running out)."""
    streak = 0
    for t in trade_history:
        if t.get("status") != "CLOSED" or t.get("pnl") is None:
            continue
        if t["pnl"] < 0:
            streak += 1
        else:
            break
    return streak


def _weekly_loss_pct(trade_history: list[dict], balance: float) -> float:
    if not balance:
        return 0.0
    cutoff = datetime.now(timezone.utc).timestamp() - 7 * 86400
    total = 0.0
    for t in trade_history:
        if t.get("status") != "CLOSED" or t.get("pnl") is None:
            continue
        closed_at = _parse_iso(t.get("closed_at"))
        if closed_at and closed_at.timestamp() >= cutoff:
            total += t["pnl"]
    return max(0.0, -total / balance * 100)


def _max_drawdown_pct(trade_history: list[dict], balance: float) -> float:
    """Approximates the worst peak-to-trough drawdown visible in the trade
    history window, by reconstructing an equity curve backwards from the
    current balance. There is no persisted equity time series to draw on,
    so this is a bounded estimate over the fetched history, not a lifetime
    figure."""
    closed = [t for t in trade_history if t.get("status") == "CLOSED" and t.get("pnl") is not None]
    if not closed:
        return 0.0
    chronological = list(reversed(closed))
    equity = balance - sum(t["pnl"] for t in chronological)
    peak = equity
    max_dd = 0.0
    for t in chronological:
        equity += t["pnl"]
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
    return max_dd


def _cooldown_remaining_minutes(trade_history: list[dict], cooldown_minutes: int) -> float:
    if cooldown_minutes <= 0 or not trade_history:
        return 0.0
    last_opened = _parse_iso(trade_history[0].get("opened_at"))
    if not last_opened:
        return 0.0
    elapsed_minutes = (datetime.now(timezone.utc) - last_opened).total_seconds() / 60
    return max(0.0, cooldown_minutes - elapsed_minutes)


def evaluate_risk(
    confidence: float,
    direction: str,
    open_positions: int,
    portfolio: dict | None = None,
    trade_history: list[dict] | None = None,
    spread_pct: float | None = None,
    volume: float | None = None,
    volume_sma20: float | None = None,
    data_reliable: bool | None = None,
    data_reason: str | None = None,
) -> dict:
    """The single authoritative paper-trading risk gate. Reads the editable
    limits fresh from settings_repository (dashboard-configurable) rather
    than the static env config, so a change from the Risk Management page
    takes effect on the very next call - no restart. Used by both the
    auto-trading scheduler (app/engine/trading_engine.py) before it routes
    an order, and available to any other caller that wants the same
    decision without duplicating the checks.

    Returns a dict with `allowed`, `reason`, and enough context (required
    confidence, the specific limit that tripped) for the frontend to explain
    *why* the bot is waiting rather than just that it is.
    """
    risk = settings_repository.get_settings(scope="paper")
    portfolio = portfolio or {}
    trade_history = trade_history or []

    required_confidence = round(risk["min_confidence_to_trade"] * 100, 1)
    balance = float(portfolio.get("balance") or 0)
    daily_pnl = float(portfolio.get("daily_pnl") or 0)

    result = {
        "allowed": False,
        "reason": "Risk checks passed",
        "confidence": confidence,
        "required_confidence": required_confidence,
        "settings": risk,
    }

    # The emergency kill switch (Phase 22) halts EVERY mode, paper included -
    # imported lazily to keep this module import-light for pure-function
    # callers (stress harness, unit tests of the helpers above).
    from app.trading import modes
    if modes.kill_switch_active():
        result["reason"] = "Kill switch active - all trading halted"
        return result

    if not risk["paper_trading_enabled"]:
        result["reason"] = "Paper trading is disabled in risk settings"
        return result

    # Same fail-open-on-unknown policy as spread/volume: None means the
    # caller had no data-quality verdict to hand; only an explicit False
    # (set by the prediction pipeline's data-quality gate) vetoes.
    if data_reliable is False:
        result["reason"] = data_reason or "Market data quality below the usable threshold - trading blocked"
        return result

    if direction not in ("LONG", "SHORT"):
        result["reason"] = "No qualifying trade signal"
        return result

    if direction == "LONG" and not risk["allow_long"]:
        result["reason"] = "Long trades disabled by risk settings"
        return result

    if direction == "SHORT" and not risk["allow_short"]:
        result["reason"] = "Short trades disabled by risk settings"
        return result

    if confidence < required_confidence:
        result["reason"] = f"Confidence {confidence:.1f}% below required {required_confidence:.1f}%"
        return result

    if open_positions >= risk["max_open_positions"]:
        result["reason"] = f"Maximum open positions reached ({risk['max_open_positions']})"
        return result

    spread_ok, spread_reason = spread_allowed(spread_pct)
    if not spread_ok:
        result["reason"] = spread_reason
        return result

    volume_ok, volume_reason = volume_allowed(volume, volume_sma20)
    if not volume_ok:
        result["reason"] = volume_reason
        return result

    if balance > 0 and daily_pnl < 0:
        daily_loss_pct = -daily_pnl / balance * 100
        if daily_loss_pct >= risk["max_daily_loss_pct"]:
            result["reason"] = f"Daily loss limit reached ({daily_loss_pct:.1f}% >= {risk['max_daily_loss_pct']}%)"
            return result

    weekly_loss_pct = _weekly_loss_pct(trade_history, balance)
    if weekly_loss_pct >= risk["max_weekly_loss_pct"]:
        result["reason"] = f"Weekly loss limit reached ({weekly_loss_pct:.1f}% >= {risk['max_weekly_loss_pct']}%)"
        return result

    drawdown_pct = _max_drawdown_pct(trade_history, balance)
    if drawdown_pct >= risk["max_drawdown_pct"]:
        result["reason"] = f"Max drawdown reached ({drawdown_pct:.1f}% >= {risk['max_drawdown_pct']}%)"
        return result

    consecutive_losses = _consecutive_losses(trade_history)
    if consecutive_losses >= risk["max_consecutive_losses"]:
        result["reason"] = f"Max consecutive losses reached ({consecutive_losses})"
        return result

    cooldown_remaining = _cooldown_remaining_minutes(trade_history, risk["cooldown_minutes"])
    if cooldown_remaining > 0:
        result["reason"] = f"Cooldown active ({cooldown_remaining:.1f}m remaining)"
        return result

    result["allowed"] = True
    return result
