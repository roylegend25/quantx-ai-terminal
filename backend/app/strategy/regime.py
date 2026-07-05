def detect(features: dict):
    """
    Detect current market regime.
    Returns one of:
      TRENDING
      RANGING
      HIGH_VOL
      NORMAL
    """

    # compute_features() always includes these keys, but their value is None
    # until enough candles exist for that indicator's rolling window (e.g.
    # bb_width needs 20, realized_volatility needs 20) - dict.get(key, 0)
    # only substitutes a default for a *missing* key, not a present-but-None
    # one, so a cold-start/short-history symbol would otherwise crash here
    # with "'<' not supported between instances of 'NoneType' and 'float'".
    trend = features.get("trend_score") or 0
    vol = features.get("realized_volatility") or 0
    bbw = features.get("bb_width") or 0

    if trend >= 3:
        return "TRENDING"

    if bbw < 0.006:
        return "RANGING"

    if vol > 0.03:
        return "HIGH_VOL"

    return "NORMAL"
