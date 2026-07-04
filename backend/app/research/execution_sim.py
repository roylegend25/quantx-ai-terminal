"""Cost/risk execution simulation - the Phase 16 "Backtest Engine Upgrade".

Takes a strategy's raw per-bar LONG/SHORT/NO_TRADE signal series
(app/research/lab_strategies.py) and replays it against the same OHLC
candles with commission, slippage, bid/ask spread, funding, partial fills,
latency and ATR-based stop-loss/take-profit - producing the same trade-dict
shape app/research/metrics.py already scores. Pure historical replay over
data already downloaded via /api/backtest/download; never touches the live
prediction or execution path (app/execution/, app/trading/).
"""

from dataclasses import dataclass

import pandas as pd


@dataclass
class CostConfig:
    commission_pct: float = 0.04  # % of notional, charged on every fill (entry + exit)
    slippage_bps: float = 2.0  # adverse price slippage in basis points, applied on every fill
    spread_bps: float = 2.0  # full bid/ask spread in basis points; each fill pays half
    funding_rate_pct: float = 0.01  # % of notional per 8h funding period, charged as a directionless holding cost
    latency_ms: float = 250.0  # signal-to-fill delay, approximated as a fraction of one bar
    partial_fill_ratio: float = 1.0  # fraction of intended notional that actually fills (1.0 = full fill)


@dataclass
class RiskConfig:
    atr_sl_mult: float = 1.5
    atr_tp_mult: float = 3.0
    entry_confidence_threshold: float = 50.0
    position_size_usd: float = 1000.0


def _bar_duration_ms(times: list[float]) -> float:
    diffs = [b - a for a, b in zip(times, times[1:]) if b > a]
    return sorted(diffs)[len(diffs) // 2] if diffs else 0.0


def _fill_price(close: float, next_open: float | None, latency_ms: float, bar_duration_ms: float) -> float:
    """Approximates latency by interpolating between this bar's close and the
    next bar's open - a signal that fills instantly lands on the close, one
    that takes a full bar to fill lands on the next bar's open."""
    if next_open is None or bar_duration_ms <= 0:
        return close
    fraction = min(1.0, max(0.0, latency_ms / bar_duration_ms))
    return close + (next_open - close) * fraction


def _apply_entry_costs(raw_price: float, side: str, cost: CostConfig) -> float:
    half_spread = raw_price * (cost.spread_bps / 20000)
    slip = raw_price * (cost.slippage_bps / 10000)
    if side == "LONG":
        return raw_price + half_spread + slip
    return raw_price - half_spread - slip


def _apply_exit_costs(raw_price: float, side: str, cost: CostConfig) -> float:
    """Closing a LONG is a sell (crosses the spread/slippage the other way);
    closing a SHORT is a buy."""
    return _apply_entry_costs(raw_price, "SHORT" if side == "LONG" else "LONG", cost)


def _open_position(direction: str, fill_price: float, entry_time, atr: float, confidence: float, cost: CostConfig, risk: RiskConfig) -> dict:
    filled_entry = _apply_entry_costs(fill_price, direction, cost)
    notional = risk.position_size_usd * cost.partial_fill_ratio
    qty = notional / filled_entry if filled_entry else 0.0
    entry_commission = notional * (cost.commission_pct / 100)

    sl_distance = atr * risk.atr_sl_mult
    tp_distance = atr * risk.atr_tp_mult
    sl = filled_entry - sl_distance if direction == "LONG" else filled_entry + sl_distance
    tp = filled_entry + tp_distance if direction == "LONG" else filled_entry - tp_distance

    return {
        "side": direction,
        "entry_price": filled_entry,
        "entry_time": entry_time,
        "qty": qty,
        "notional": notional,
        "sl": sl,
        "tp": tp,
        "risk_amount": sl_distance * qty,
        "confidence": confidence,
        "entry_commission": entry_commission,
    }


def _close_position(position: dict, raw_exit_price: float, exit_time, exit_reason: str, cost: CostConfig) -> dict:
    side = position["side"]
    filled_exit = _apply_exit_costs(raw_exit_price, side, cost)
    exit_notional = position["qty"] * filled_exit
    exit_commission = exit_notional * (cost.commission_pct / 100)

    holding_seconds = (
        (exit_time - position["entry_time"]).total_seconds()
        if position["entry_time"] is not None and exit_time is not None
        else 0.0
    )
    funding = position["notional"] * (cost.funding_rate_pct / 100) * (holding_seconds / 3600 / 8)

    gross_pnl = (
        (filled_exit - position["entry_price"]) * position["qty"]
        if side == "LONG"
        else (position["entry_price"] - filled_exit) * position["qty"]
    )
    pnl = gross_pnl - position["entry_commission"] - exit_commission - funding

    return {
        "pnl": round(pnl, 4),
        "entry_time": position["entry_time"],
        "exit_time": exit_time,
        "confidence": position["confidence"],
        "correct": pnl >= 0,
        "side": side,
        "entry_price": round(position["entry_price"], 6),
        "exit_price": round(filled_exit, 6),
        "r_multiple": round(pnl / position["risk_amount"], 4) if position["risk_amount"] else None,
        "exit_reason": exit_reason,
        "commission": round(position["entry_commission"] + exit_commission, 4),
        "funding": round(funding, 4),
    }


def simulate_trades(
    df: pd.DataFrame,
    signals: list[tuple[str, float]],
    cost: CostConfig | None = None,
    risk: RiskConfig | None = None,
) -> list[dict]:
    """`signals[i]` is the (direction, confidence) a strategy produced for
    `df.iloc[i]`; must be the same length as `df`. Returns closed trades in
    the shape app/research/metrics.py expects, each carrying extra
    "r_multiple" (pnl / initial SL risk), "exit_reason", "commission" and
    "funding" fields for the Research Lab's trade list."""
    cost = cost or CostConfig()
    risk = risk or RiskConfig()

    has_time = "time" in df.columns
    times = df["time"].astype(float).tolist() if has_time else []
    bar_duration_ms = _bar_duration_ms(times)

    def _time_at(i: int):
        return pd.to_datetime(df.iloc[i]["time"], unit="ms", utc=True) if has_time else None

    trades: list[dict] = []
    position: dict | None = None
    n = len(df)

    for i in range(n):
        row = df.iloc[i]
        direction, confidence = signals[i]
        if confidence < risk.entry_confidence_threshold:
            direction = "NO_TRADE"

        close = float(row["close"])
        high = float(row.get("high", close))
        low = float(row.get("low", close))
        atr = float(row.get("atr") or 0.0) or close * 0.01
        next_open = float(df.iloc[i + 1]["open"]) if i + 1 < n and "open" in df.columns else None
        fill_price = _fill_price(close, next_open, cost.latency_ms, bar_duration_ms)

        if position is not None:
            side = position["side"]
            exit_price, exit_reason = None, None

            # intra-bar SL/TP check using this bar's high/low; a stop is
            # assumed to trigger before a target on the same bar (conservative)
            if side == "LONG":
                if low <= position["sl"]:
                    exit_price, exit_reason = position["sl"], "stop_loss"
                elif high >= position["tp"]:
                    exit_price, exit_reason = position["tp"], "take_profit"
            else:
                if high >= position["sl"]:
                    exit_price, exit_reason = position["sl"], "stop_loss"
                elif low <= position["tp"]:
                    exit_price, exit_reason = position["tp"], "take_profit"

            if exit_price is None and direction != side and direction != "NO_TRADE":
                exit_price, exit_reason = fill_price, "signal_flip"

            if exit_price is not None:
                trades.append(_close_position(position, exit_price, _time_at(i), exit_reason, cost))
                position = None

        if position is None and direction in ("LONG", "SHORT"):
            position = _open_position(direction, fill_price, _time_at(i), atr, confidence, cost, risk)

    if position is not None:
        trades.append(_close_position(position, float(df.iloc[-1]["close"]), _time_at(n - 1), "end_of_data", cost))

    return trades
