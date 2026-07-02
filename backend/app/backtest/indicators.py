import pandas as pd

def add_indicators(df: pd.DataFrame):

    df = df.copy()

    # EMA
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()

    # RSI
    delta = df["close"].diff()

    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()

    rs = gain / loss.replace(0, 1e-9)
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = df["close"].ewm(span=12).mean()
    ema26 = df["close"].ewm(span=26).mean()

    macd = ema12 - ema26
    signal = macd.ewm(span=9).mean()

    df["macd"] = macd
    df["macd_signal"] = signal
    df["macd_hist"] = macd - signal

    # ATR
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()

    tr = pd.concat(
        [high_low, high_close, low_close],
        axis=1
    ).max(axis=1)

    df["atr"] = tr.rolling(14).mean()

    # Bollinger Bands
    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std()

    upper = sma20 + std20 * 2
    lower = sma20 - std20 * 2

    df["bb_width"] = (upper - lower) / sma20

    return df.fillna(method="bfill").fillna(method="ffill")
