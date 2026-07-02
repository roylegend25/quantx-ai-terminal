from typing import Dict

def evaluate(features: Dict):
    """
    Trend Following Strategy

    Returns:
        {
            direction: LONG | SHORT | NO_TRADE
            confidence: float
            reason: str
        }
    """

    score = 0
    reasons = []

    price = features["price"]
    ema20 = features["ema20"]
    ema50 = features["ema50"]
    ema200 = features["ema200"]

    if price > ema20:
        score += 1
        reasons.append("Price > EMA20")
    else:
        score -= 1
        reasons.append("Price < EMA20")

    if ema20 > ema50:
        score += 1
        reasons.append("EMA20 > EMA50")
    else:
        score -= 1
        reasons.append("EMA20 < EMA50")

    if ema50 > ema200:
        score += 1
        reasons.append("EMA50 > EMA200")
    else:
        score -= 1
        reasons.append("EMA50 < EMA200")

    confidence = abs(score) / 3 * 100

    if score >= 2:
        direction = "LONG"
    elif score <= -2:
        direction = "SHORT"
    else:
        direction = "NO_TRADE"

    return {
        "direction": direction,
        "confidence": round(confidence, 1),
        "reason": ", ".join(reasons),
    }
