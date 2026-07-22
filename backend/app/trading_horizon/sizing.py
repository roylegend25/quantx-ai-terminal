"""Server-side, reduction-only sizing for persisted Horizon authorities."""
from __future__ import annotations

from app.core.config import settings
from app.db.models import Portfolio, Trade
from app.risk import settings_repository


class PositionSizingError(ValueError):
    pass


def calculate_position_size(db, *, user_id: str, symbol: str, risk_approval: dict,
                            mode: str, requested_notional: float | None = None) -> dict:
    scope = "binance_real" if mode in {"BINANCE_LIVE", "BINANCE_TESTNET"} else "paper"
    try:
        current = settings_repository.get_user_settings(user_id, scope=scope, db=db)
        current_user_limit = float(current["max_position_size_usd"])
        current_risk_pct = float(current["max_risk_per_trade_pct"])
        persisted_limit = float(risk_approval["approved_notional_ceiling_usd"])
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise PositionSizingError("RISK_POLICY_UNAVAILABLE") from exc
    if (risk_approval.get("user_id") != user_id or risk_approval.get("symbol") != symbol
            or current.get("risk_policy_owner") != user_id):
        raise PositionSizingError("RISK_POLICY_UNAVAILABLE")
    if min(current_user_limit, current_risk_pct, persisted_limit) <= 0:
        raise PositionSizingError("POSITION_SIZE_LIMIT_ZERO")

    portfolio = db.get(Portfolio, 1)
    if portfolio is None:
        raise PositionSizingError("RISK_POLICY_UNAVAILABLE")
    equity = float(portfolio.equity if portfolio.equity is not None else portfolio.balance or 0)
    if equity <= 0:
        raise PositionSizingError("INSUFFICIENT_MARGIN")
    trades = db.query(Trade).filter(Trade.status == "OPEN", Trade.user_id == user_id).all()
    portfolio_exposure = sum(abs(float(trade.entry or 0) * float(trade.qty or 0)) for trade in trades)
    symbol_exposure = sum(abs(float(trade.entry or 0) * float(trade.qty or 0))
                          for trade in trades if trade.symbol == symbol)
    portfolio_limit = current_user_limit * int(current.get("max_open_positions") or 1)
    portfolio_remaining = max(0.0, portfolio_limit - portfolio_exposure)
    symbol_remaining = max(0.0, current_user_limit - symbol_exposure)
    stop_pct = risk_approval.get("stop_distance_pct")
    current_risk_budget = equity * current_risk_pct / 100
    risk_based = (current_risk_budget / (float(stop_pct) / 100)
                  if isinstance(stop_pct, (int, float)) and stop_pct > 0 else current_user_limit)

    ceilings = {
        "PERSISTED_AUTHORITY_LIMIT": persisted_limit,
        "CURRENT_USER_LIMIT": current_user_limit,
        "RISK_BASED_LIMIT": risk_based,
        "PORTFOLIO_EXPOSURE_LIMIT": portfolio_remaining,
        "SYMBOL_EXPOSURE_LIMIT": symbol_remaining,
        "ACCOUNT_AVAILABLE_LIMIT": equity,
    }
    if isinstance(requested_notional, (int, float)):
        ceilings["REQUESTED_RISK_BUDGET"] = float(requested_notional)
    exchange_limit = None
    if mode in {"BINANCE_LIVE", "BINANCE_TESTNET"}:
        exchange_limit = float(settings.binance_max_notional_per_trade)
        ceilings["EXCHANGE_MAX_NOTIONAL_LIMIT"] = exchange_limit
    positive = {key: value for key, value in ceilings.items() if isinstance(value, (int, float))}
    limiting_factor, final = min(positive.items(), key=lambda item: item[1])
    if final <= 0:
        code = limiting_factor if limiting_factor in {"PORTFOLIO_EXPOSURE_LIMIT", "SYMBOL_EXPOSURE_LIMIT"} else "POSITION_SIZE_LIMIT_ZERO"
        raise PositionSizingError(code)
    return {
        "configured_user_limit_usd": current_user_limit,
        "persisted_authority_limit_usd": persisted_limit,
        "risk_based_limit_usd": risk_based,
        "portfolio_exposure_remaining_usd": portfolio_remaining,
        "symbol_exposure_remaining_usd": symbol_remaining,
        "account_available_limit_usd": equity,
        "paper_account_available_limit_usd": equity if mode == "PAPER" else None,
        "exchange_limit_usd": exchange_limit,
        "final_approved_notional_usd": final,
        "limiting_factor": limiting_factor,
        "persisted_risk_policy_revision": risk_approval.get("risk_policy_revision"),
        "current_risk_policy_revision": current.get("updated_at") or "risk-settings:initial",
    }


def floor_quantity_to_step(approved_notional: float, price: float, step: float) -> tuple[float, float]:
    if min(approved_notional, price, step) <= 0:
        raise PositionSizingError("POSITION_SIZE_LIMIT_ZERO")
    raw = approved_notional / price
    quantity = int(raw / step) * step
    actual = quantity * price
    if quantity <= 0 or actual > approved_notional + 1e-9:
        raise PositionSizingError("EXCHANGE_MIN_NOTIONAL_NOT_MET")
    return quantity, actual
