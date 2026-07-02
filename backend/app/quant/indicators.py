import pandas as pd
import numpy as np

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def macd(close):
    fast = ema(close, 12)
    slow = ema(close, 26)
    line = fast - slow
    signal = ema(line, 9)
    hist = line - signal
    return line, signal, hist

def atr(df, period=14):
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def bollinger(close, period=20, std_mult=2):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width = (upper - lower) / mid
    return upper, mid, lower, width

def compute_features(candles):
    df = pd.DataFrame(candles)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])

    close = df["close"]

    macd_line, macd_signal, macd_hist = macd(close)
    bb_upper, bb_mid, bb_lower, bb_width = bollinger(close)

    df["ema20"] = ema(close, 20)
    df["ema50"] = ema(close, 50)
    df["ema200"] = ema(close, 200)
    df["rsi"] = rsi(close)
    df["macd"] = macd_line
    df["macd_signal"] = macd_signal
    df["macd_hist"] = macd_hist
    df["atr"] = atr(df)
    df["bb_upper"] = bb_upper
    df["bb_mid"] = bb_mid
    df["bb_lower"] = bb_lower
    df["bb_width"] = bb_width
    df["return_1"] = close.pct_change()
    df["realized_volatility"] = df["return_1"].rolling(20).std() * np.sqrt(288)

    last = df.iloc[-1].replace({np.nan: None})

    trend_score = 0
    if last["close"] > last["ema20"]:
        trend_score += 1
    if last["ema20"] > last["ema50"]:
        trend_score += 1
    if last["ema50"] > last["ema200"]:
        trend_score += 1

    if trend_score >= 2:
        regime = "bullish_trend"
    elif trend_score <= 0:
        regime = "bearish_or_weak"
    else:
        regime = "sideways"

    return {
        "symbol_features": {
            "price": float(last["close"]),
            "ema20": None if last["ema20"] is None else float(last["ema20"]),
            "ema50": None if last["ema50"] is None else float(last["ema50"]),
            "ema200": None if last["ema200"] is None else float(last["ema200"]),
            "rsi": None if last["rsi"] is None else float(last["rsi"]),
            "macd": None if last["macd"] is None else float(last["macd"]),
            "macd_signal": None if last["macd_signal"] is None else float(last["macd_signal"]),
            "macd_hist": None if last["macd_hist"] is None else float(last["macd_hist"]),
            "atr": None if last["atr"] is None else float(last["atr"]),
            "bb_width": None if last["bb_width"] is None else float(last["bb_width"]),
            "realized_volatility": None if last["realized_volatility"] is None else float(last["realized_volatility"]),
            "trend_score": trend_score,
            "regime": regime,
        }
    }
