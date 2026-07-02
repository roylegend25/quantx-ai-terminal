def detect(features: dict):
    """
    Detect current market regime.
    Returns one of:
      TRENDING
      RANGING
      HIGH_VOL
      NORMAL
    """

    trend = features.get("trend_score", 0)
    vol = features.get("realized_volatility", 0)
    bbw = features.get("bb_width", 0)

    if trend >= 3:
        return "TRENDING"

    if bbw < 0.006:
        return "RANGING"

    if vol > 0.03:
        return "HIGH_VOL"

    return "NORMAL"
