from dataclasses import dataclass
from app.core.config import settings

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
    if confidence < settings.confidence_threshold:
        return False, "Confidence below threshold"

    if open_positions >= settings.max_open_positions:
        return False, "Maximum open positions reached"

    return True, "Risk checks passed"
