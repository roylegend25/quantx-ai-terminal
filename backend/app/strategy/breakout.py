from typing import Dict

def evaluate(features: Dict):
    """
    Breakout Strategy
    Confirms a volatility breakout using Bollinger Band width expansion,
    displacement from EMA20 (in ATR units), and MACD histogram confirmation.
    """

    price = features.get("price")
    ema20 = features.get("ema20")
    atr = features.get("atr")
    bb_width = features.get("bb_width")
    macd_hist = features.get("macd_hist")

    if price is None or ema20 is None or not atr or bb_width is None:
        return {
            "direction": "NO_TRADE",
            "confidence": 0,
            "reason": "Missing indicators",
        }

    score = 0
    reasons = []

    displacement = (price - ema20) / atr
    vol_expansion = bb_width > 0.02

    if vol_expansion:
        reasons.append("Volatility expansion (BB width > 2%)")
    else:
        reasons.append("No volatility expansion")

    if displacement > 1.0:
        score += 1
        reasons.append("Price broke above EMA20 by > 1 ATR")
        if vol_expansion:
            score += 1
            reasons.append("Volatility expansion confirms upside breakout")
    elif displacement < -1.0:
        score -= 1
        reasons.append("Price broke below EMA20 by > 1 ATR")
        if vol_expansion:
            score -= 1
            reasons.append("Volatility expansion confirms downside breakout")
    else:
        reasons.append("No decisive breakout displacement")

    if macd_hist is not None:
        if macd_hist > 0 and displacement > 0:
            score += 1
            reasons.append("MACD histogram confirms upside breakout")
        elif macd_hist < 0 and displacement < 0:
            score -= 1
            reasons.append("MACD histogram confirms downside breakout")

    confidence = min(abs(score) / 3 * 100, 100)

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
