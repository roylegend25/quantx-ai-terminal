from app.strategy import breakout

BASE_FEATURES = {
    "price": 100.0,
    "ema20": 95.0,
    "atr": 2.0,
    "bb_width": 0.03,
    "macd_hist": 1.0,
}


def test_long_breakout():
    result = breakout.evaluate(BASE_FEATURES)
    assert result["direction"] == "LONG"
    assert result["confidence"] > 0


def test_short_breakout():
    features = dict(BASE_FEATURES, price=90.0, ema20=95.0, macd_hist=-1.0)
    result = breakout.evaluate(features)
    assert result["direction"] == "SHORT"


def test_no_trade_when_no_expansion_or_displacement():
    features = dict(BASE_FEATURES, price=95.5, ema20=95.0, bb_width=0.005, macd_hist=0.0)
    result = breakout.evaluate(features)
    assert result["direction"] == "NO_TRADE"


def test_missing_indicators_returns_no_trade():
    result = breakout.evaluate({"price": 100.0})
    assert result["direction"] == "NO_TRADE"
    assert result["confidence"] == 0
