"""Multi-timeframe feature engine (Phase 20).

Computes the full research feature set for any stored (symbol, timeframe)
candle series - price/volume transforms, the standard indicator suite (ATR,
RSI, MACD, EMA/SMA ladders, VWAP, Bollinger, ADX, Supertrend, Ichimoku),
structure features (pivots, support/resistance, volume profile), and the
derivative/sentiment joins (funding rate, open-interest change, liquidation
cluster strength) where that data has been downloaded. Nothing is invented:
a feature whose source data isn't in the DB is simply null.

Rows are persisted to feature_snapshots keyed by
(symbol, timeframe, candle timestamp, feature_version).
"""

import math

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.data_sources.downloader import load_candles
from app.data_sources.normalizer import timeframe_ms as tf_ms
from app.db.models import (
    FeatureSnapshotRow,
    FundingRateRow,
    LiquidationEstimateRow,
    OpenInterestRow,
    OrderbookSnapshotRow,
    SentimentRow,
)
from app.db.session import SessionLocal

FEATURE_VERSION = "v1"

MIN_BARS = 60  # below this the indicator suite is statistically meaningless


class InsufficientDataError(Exception):
    pass


# ------------------------------------------------------------- indicators

def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False).mean()


def _true_range(df: pd.DataFrame) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    return pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)


def _adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = _wilder(_true_range(df), period)
    plus_di = 100 * _wilder(pd.Series(plus_dm, index=df.index), period) / tr.replace(0, np.nan)
    minus_di = 100 * _wilder(pd.Series(minus_dm, index=df.index), period) / tr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame({"adx": _wilder(dx.fillna(0.0), period), "plus_di": plus_di, "minus_di": minus_di})


def _supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    atr = _wilder(_true_range(df), period)
    hl2 = (df["high"] + df["low"]) / 2
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    st = pd.Series(np.nan, index=df.index)
    direction = pd.Series(1, index=df.index)
    final_upper, final_lower = upper.copy(), lower.copy()

    for i in range(1, len(df)):
        if df["close"].iloc[i - 1] > final_upper.iloc[i - 1]:
            final_upper.iloc[i] = upper.iloc[i]
        else:
            final_upper.iloc[i] = min(upper.iloc[i], final_upper.iloc[i - 1])
        if df["close"].iloc[i - 1] < final_lower.iloc[i - 1]:
            final_lower.iloc[i] = lower.iloc[i]
        else:
            final_lower.iloc[i] = max(lower.iloc[i], final_lower.iloc[i - 1])

        if df["close"].iloc[i] > final_upper.iloc[i]:
            direction.iloc[i] = 1
        elif df["close"].iloc[i] < final_lower.iloc[i]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
        st.iloc[i] = final_lower.iloc[i] if direction.iloc[i] == 1 else final_upper.iloc[i]

    return pd.DataFrame({"supertrend": st, "supertrend_direction": direction})


def _ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    tenkan = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    kijun = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
    return pd.DataFrame(
        {"ichimoku_tenkan": tenkan, "ichimoku_kijun": kijun, "ichimoku_span_a": span_a, "ichimoku_span_b": span_b}
    )


def _volume_profile_poc(df: pd.DataFrame, window: int = 100, bins: int = 24) -> pd.Series:
    """Rolling point-of-control: the price level that traded the most volume
    over the trailing window, as a % distance from the current close."""
    poc = pd.Series(np.nan, index=df.index)
    closes = df["close"].to_numpy()
    volumes = df["volume"].to_numpy()
    for i in range(window, len(df)):
        window_close = closes[i - window : i]
        window_vol = volumes[i - window : i]
        lo, hi = window_close.min(), window_close.max()
        if hi <= lo:
            continue
        hist, edges = np.histogram(window_close, bins=bins, range=(lo, hi), weights=window_vol)
        center = (edges[hist.argmax()] + edges[hist.argmax() + 1]) / 2
        poc.iloc[i] = (closes[i] - center) / closes[i] * 100
    return poc


def compute_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """df: canonical candle columns (time/open/high/low/close/volume),
    ascending. Returns a same-length frame of features."""
    if len(df) < MIN_BARS:
        raise InsufficientDataError(f"Need at least {MIN_BARS} candles, have {len(df)}")

    out = pd.DataFrame(index=df.index)
    close = df["close"]

    out["time"] = df["time"]
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = df[col]

    out["return_pct"] = close.pct_change() * 100
    out["log_return"] = np.log(close / close.shift()).replace([np.inf, -np.inf], np.nan)
    out["volatility"] = out["log_return"].rolling(20).std() * 100
    out["atr"] = _wilder(_true_range(df), 14)
    out["atr_pct"] = out["atr"] / close * 100

    delta = close.diff()
    gain = _wilder(delta.clip(lower=0), 14)
    loss = _wilder(-delta.clip(upper=0), 14)
    out["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))

    ema12, ema26 = close.ewm(span=12).mean(), close.ewm(span=26).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    for n in (20, 50, 100, 200):
        out[f"ema{n}"] = close.ewm(span=n).mean()
        out[f"sma{n}"] = close.rolling(n).mean()

    typical = (df["high"] + df["low"] + close) / 3
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    out["vwap"] = (typical * df["volume"]).cumsum() / cum_vol

    sma20, std20 = close.rolling(20).mean(), close.rolling(20).std()
    out["bb_upper"] = sma20 + 2 * std20
    out["bb_lower"] = sma20 - 2 * std20
    out["bb_width"] = (out["bb_upper"] - out["bb_lower"]) / sma20
    band_range = (out["bb_upper"] - out["bb_lower"]).replace(0, np.nan)
    out["bb_percent_b"] = (close - out["bb_lower"]) / band_range

    adx = _adx(df)
    out["adx"], out["plus_di"], out["minus_di"] = adx["adx"], adx["plus_di"], adx["minus_di"]

    st = _supertrend(df)
    out["supertrend"], out["supertrend_direction"] = st["supertrend"], st["supertrend_direction"]

    ichi = _ichimoku(df)
    for col in ichi.columns:
        out[col] = ichi[col]
    out["above_cloud"] = (close > ichi[["ichimoku_span_a", "ichimoku_span_b"]].max(axis=1)).astype(float)

    out["volume_profile_poc_dist_pct"] = _volume_profile_poc(df)

    out["resistance"] = df["high"].rolling(50).max()
    out["support"] = df["low"].rolling(50).min()
    out["dist_to_resistance_pct"] = (out["resistance"] - close) / close * 100
    out["dist_to_support_pct"] = (close - out["support"]) / close * 100

    prev_h, prev_l, prev_c = df["high"].shift(), df["low"].shift(), close.shift()
    pivot = (prev_h + prev_l + prev_c) / 3
    out["pivot"] = pivot
    out["pivot_r1"] = 2 * pivot - prev_l
    out["pivot_s1"] = 2 * pivot - prev_h

    # -------- composite scores (0-100) --------
    ema_alignment = (
        (out["ema20"] > out["ema50"]).astype(float)
        + (out["ema50"] > out["ema100"]).astype(float)
        + (out["ema100"] > out["ema200"]).astype(float)
    ) / 3
    trend_direction = np.sign(out["ema20"] - out["ema200"]).fillna(0)
    adx_strength = (out["adx"].clip(0, 50) / 50).fillna(0)
    st_agree = (out["supertrend_direction"] == trend_direction).astype(float)
    out["trend_score"] = (100 * (0.5 * adx_strength + 0.3 * ema_alignment + 0.2 * st_agree)).round(2)

    rsi_push = ((out["rsi"] - 50).abs() / 50).clip(0, 1)
    macd_push = (out["macd_hist"].abs() / (out["atr"].replace(0, np.nan))).clip(0, 1).fillna(0)
    out["momentum_score"] = (100 * (0.6 * rsi_push + 0.4 * macd_push)).round(2)

    bb_extreme = ((out["bb_percent_b"] - 0.5).abs() * 2).clip(0, 1).fillna(0)
    rsi_extreme = (((out["rsi"] - 50).abs() - 20).clip(lower=0) / 30).clip(0, 1)
    out["mean_reversion_score"] = (100 * (0.6 * bb_extreme + 0.4 * rsi_extreme)).round(2)

    # -------- regime classification --------
    vol_z = (out["volatility"] - out["volatility"].rolling(100).mean()) / out["volatility"].rolling(100).std()
    conditions = [
        vol_z > 1.5,
        (out["adx"] > 25) & (trend_direction > 0),
        (out["adx"] > 25) & (trend_direction < 0),
    ]
    out["regime"] = np.select(conditions, ["HIGH_VOLATILITY", "TRENDING_UP", "TRENDING_DOWN"], default="RANGING")

    return out


# ------------------------------------------------------------- enrichment

def _asof_join(times: pd.Series, rows: list[tuple[int, float]]) -> pd.Series:
    """For each candle time, the most recent value at or before it."""
    if not rows:
        return pd.Series(np.nan, index=times.index)
    rows = sorted(rows)
    ts = np.array([r[0] for r in rows], dtype="int64")
    vals = np.array([r[1] for r in rows], dtype="float64")
    idx = np.searchsorted(ts, times.to_numpy(dtype="int64"), side="right") - 1
    result = np.where(idx >= 0, vals[np.clip(idx, 0, len(vals) - 1)], np.nan)
    return pd.Series(result, index=times.index)


def enrich_with_derivatives(out: pd.DataFrame, symbol: str, db: Session) -> pd.DataFrame:
    t_min, t_max = int(out["time"].min()), int(out["time"].max())

    funding_rows = [
        (r.timestamp, r.funding_rate)
        for r in db.query(FundingRateRow)
        .filter(FundingRateRow.symbol == symbol, FundingRateRow.timestamp <= t_max)
        .order_by(FundingRateRow.timestamp.asc())
        .all()
    ]
    out["funding_rate"] = _asof_join(out["time"], funding_rows)

    oi_rows = [
        (r.timestamp, r.open_interest)
        for r in db.query(OpenInterestRow)
        .filter(OpenInterestRow.symbol == symbol, OpenInterestRow.timestamp <= t_max)
        .order_by(OpenInterestRow.timestamp.asc())
        .all()
    ]
    oi = _asof_join(out["time"], oi_rows)
    out["open_interest"] = oi
    out["oi_change_pct"] = oi.pct_change(fill_method=None) * 100

    liq_rows = [
        (r.timestamp, r.strength if r.strength is not None else 0.0)
        for r in db.query(LiquidationEstimateRow)
        .filter(
            LiquidationEstimateRow.symbol == symbol,
            LiquidationEstimateRow.timestamp >= t_min,
            LiquidationEstimateRow.timestamp <= t_max,
        )
        .all()
    ]
    out["liquidation_cluster_strength"] = _asof_join(out["time"], liq_rows)

    fg_rows = [
        (r.timestamp, r.value)
        for r in db.query(SentimentRow)
        .filter(SentimentRow.metric == "fear_greed", SentimentRow.timestamp <= t_max)
        .order_by(SentimentRow.timestamp.asc())
        .all()
    ]
    out["fear_greed"] = _asof_join(out["time"], fg_rows)

    latest_ob = (
        db.query(OrderbookSnapshotRow)
        .filter(OrderbookSnapshotRow.symbol == symbol)
        .order_by(OrderbookSnapshotRow.timestamp.desc())
        .first()
    )
    # point-in-time only: applies to the newest bar, not history
    out["orderbook_imbalance"] = np.nan
    if latest_ob is not None and len(out):
        out.loc[out.index[-1], "orderbook_imbalance"] = latest_ob.imbalance

    return out


# ------------------------------------------------------------- persistence

def _row_features(row: pd.Series) -> dict:
    features = {}
    for key, value in row.items():
        if key == "time":
            continue
        if isinstance(value, (np.floating, float)):
            features[key] = None if (value is None or math.isnan(value) or math.isinf(value)) else round(float(value), 8)
        elif isinstance(value, (np.integer, int)):
            features[key] = int(value)
        else:
            features[key] = value
    return features


def generate_and_store(
    symbol: str,
    timeframe: str,
    limit: int = 500,
    store_last: int = 200,
    db: Session | None = None,
) -> dict:
    """Compute features over up to `limit` stored candles and persist the
    most recent `store_last` rows to feature_snapshots. Returns a summary
    plus the latest feature row."""
    symbol = symbol.upper()
    tf_ms(timeframe)  # validates

    owns = db is None
    db = db or SessionLocal()
    try:
        candles = load_candles(symbol, timeframe, limit=limit, db=db)
        if len(candles) < MIN_BARS:
            raise InsufficientDataError(
                f"Only {len(candles)} candles stored for {symbol} {timeframe} - "
                f"need {MIN_BARS}. Download candles first (POST /api/data/download)."
            )
        df = pd.DataFrame(candles)
        quality = df["quality_score"].dropna().iloc[-1] if df["quality_score"].notna().any() else None

        frame = compute_feature_frame(df)
        frame = enrich_with_derivatives(frame, symbol, db)

        tail = frame.tail(store_last)
        times = [int(t) for t in tail["time"]]
        existing = {
            t[0]
            for t in db.query(FeatureSnapshotRow.timestamp)
            .filter(
                FeatureSnapshotRow.symbol == symbol,
                FeatureSnapshotRow.timeframe == timeframe,
                FeatureSnapshotRow.feature_version == FEATURE_VERSION,
                FeatureSnapshotRow.timestamp >= min(times),
                FeatureSnapshotRow.timestamp <= max(times),
            )
            .all()
        }
        stored = 0
        for _, row in tail.iterrows():
            ts = int(row["time"])
            if ts in existing:
                continue
            db.add(
                FeatureSnapshotRow(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=ts,
                    feature_version=FEATURE_VERSION,
                    features=_row_features(row),
                    provider="binance_futures",
                    quality_score=float(quality) if quality is not None else None,
                )
            )
            stored += 1
        db.commit()

        latest = _row_features(frame.iloc[-1])
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "bars_used": len(df),
            "rows_stored": stored,
            "feature_version": FEATURE_VERSION,
            "quality_score": float(quality) if quality is not None else None,
            "feature_count": len(latest),
            "latest": latest,
        }
    finally:
        if owns:
            db.close()


def load_snapshots(
    symbol: str,
    timeframe: str,
    limit: int = 200,
    db: Session | None = None,
) -> list[dict]:
    owns = db is None
    db = db or SessionLocal()
    try:
        rows = (
            db.query(FeatureSnapshotRow)
            .filter(
                FeatureSnapshotRow.symbol == symbol.upper(),
                FeatureSnapshotRow.timeframe == timeframe,
                FeatureSnapshotRow.feature_version == FEATURE_VERSION,
            )
            .order_by(FeatureSnapshotRow.timestamp.desc())
            .limit(limit)
            .all()
        )
        rows.reverse()
        return [
            {"timestamp": r.timestamp, "features": r.features, "quality_score": r.quality_score}
            for r in rows
        ]
    finally:
        if owns:
            db.close()
