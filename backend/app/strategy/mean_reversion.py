from typing import Dict

def evaluate(features: Dict):
    """
    Mean Reversion Strategy
    Uses RSI and Bollinger Band width.
    """

    score = 0
    reasons = []

    rsi = features.get("rsi")
    bb_width = features.get("bb_width")

    if rsi is None or bb_width is None:
        return {
            "direction": "NO_TRADE",
            "confidence": 0,
            "reason": "Missing indicators",
        }

    if bb_width < 0.01:
        reasons.append("Low volatility")

        if rsi < 30:
            score += 2
            reasons.append("Oversold")
        elif rsi > 70:
            score -= 2
            reasons.append("Overbought")
        else:
            reasons.append("RSI neutral")
    else:
        reasons.append("Volatility too high")

    confidence = min(abs(score) * 50, 100)

    if score > 0:
        direction = "LONG"
    elif score < 0:
        direction = "SHORT"
    else:
        direction = "NO_TRADE"

    return {
        "direction": direction,
        "confidence": confidence,
        "reason": ", ".join(reasons),
    }
