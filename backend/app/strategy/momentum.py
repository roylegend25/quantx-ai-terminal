from typing import Dict

def evaluate(features: Dict):
    """
    Momentum Strategy using RSI and MACD histogram.
    """

    score = 0
    reasons = []

    rsi = features.get("rsi")
    macd_hist = features.get("macd_hist")

    if rsi is not None:
        if rsi > 60:
            score += 1
            reasons.append("RSI bullish > 60")
        elif rsi < 40:
            score -= 1
            reasons.append("RSI bearish < 40")
        else:
            reasons.append("RSI neutral")

    if macd_hist is not None:
        if macd_hist > 0:
            score += 1
            reasons.append("MACD histogram positive")
        elif macd_hist < 0:
            score -= 1
            reasons.append("MACD histogram negative")

    confidence = abs(score) / 2 * 100

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
