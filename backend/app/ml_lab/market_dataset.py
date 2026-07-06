"""Market-history training dataset for the AI Model Lab.

Builds a labeled frame directly from Binance USDT-M klines: one row per
closed bar, features computed with the SAME pandas indicator functions the
live prediction path uses (app/quant/indicators.py - ema/rsi/macd/atr/
bollinger are reused, not reimplemented), labeled by what price actually
did over the next `horizon` bars. Nothing is simulated or synthesized -
every feature value comes from real OHLCV history and every label from the
realized forward return.

This exists alongside (not instead of) the trade-outcome dataset in
app/ml/dataset_builder.py: that one is limited to predictions a paper trade
actually resolved (a handful of rows until the bot has traded for weeks),
while this one yields thousands of samples immediately, which is what makes
model training meaningful today. Which source a training job uses is an
explicit, recorded choice (MLOpsModel.dataset_source).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from app.quant.indicators import atr as atr_fn
from app.quant.indicators import bollinger, ema, macd, rsi

BINANCE_FAPI = "https://fapi.binance.com"

CACHE_DIR = Path("/app/data/ml_lab/datasets")

# Longest indicator lookback (ema200) - earlier rows have NaN features and
# are dropped rather than imputed.
WARMUP_BARS = 200

# Order matters: model feature vectors are saved with this exact ordering
# (MLOpsModel.feature_list) and inference/explain rebuilds vectors from it.
FEATURE_COLUMNS = [
    "ema20",
    "ema50",
    "ema200",
    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",
    "atr",
    "bb_width",
    "realized_volatility",
    "volume_ratio",
    "trend_score",
    "regime_bullish_trend",
    "regime_bearish_or_weak",
    "regime_sideways",
]

LABEL_COLUMN = "label"

DEFAULT_SPEC = {
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "timeframe": "1h",
    "bars": 1500,
    "horizon": 4,
}

MIN_ROWS = 200


class DatasetUnavailableError(RuntimeError):
    """Raised when the requested dataset cannot be built (feed down, too few
    bars) - carries the exact reason so the API/UI can surface it."""


def spec_key(spec: dict) -> str:
    canonical = json.dumps(spec, sort_keys=True)
    return hashlib.sha1(canonical.encode()).hexdigest()[:16]


def fetch_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    with httpx.Client(timeout=30) as client:
        r = client.get(
            f"{BINANCE_FAPI}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": min(int(limit), 1500)},
        )
        r.raise_for_status()
        rows = r.json()

    if not rows:
        raise DatasetUnavailableError(f"Binance returned zero {interval} klines for {symbol}")

    df = pd.DataFrame(
        {
            "time": [int(k[0]) for k in rows],
            "open": [float(k[1]) for k in rows],
            "high": [float(k[2]) for k in rows],
            "low": [float(k[3]) for k in rows],
            "close": [float(k[4]) for k in rows],
            "volume": [float(k[5]) for k in rows],
        }
    )
    # the final kline is still forming - only closed bars are usable
    return df.iloc[:-1].reset_index(drop=True)


def compute_feature_frame(candles: pd.DataFrame) -> pd.DataFrame:
    """Per-bar feature matrix using the exact indicator definitions the live
    path uses. Vectorized over the whole frame - identical math to
    compute_features(), which computes these same columns then keeps only
    the last row."""
    df = candles.copy()
    close = df["close"]

    macd_line, macd_signal, macd_hist = macd(close)
    _, _, _, bb_width = bollinger(close)

    df["ema20"] = ema(close, 20)
    df["ema50"] = ema(close, 50)
    df["ema200"] = ema(close, 200)
    df["rsi"] = rsi(close)
    df["macd"] = macd_line
    df["macd_signal"] = macd_signal
    df["macd_hist"] = macd_hist
    df["atr"] = atr_fn(df)
    df["bb_width"] = bb_width
    df["realized_volatility"] = close.pct_change().rolling(20).std() * np.sqrt(288)
    df["volume_ratio"] = df["volume"] / df["volume"].rolling(20).mean()

    trend_score = (
        (close > df["ema20"]).astype(int)
        + (df["ema20"] > df["ema50"]).astype(int)
        + (df["ema50"] > df["ema200"]).astype(int)
    )
    df["trend_score"] = trend_score
    df["regime_bullish_trend"] = (trend_score >= 2).astype(float)
    df["regime_bearish_or_weak"] = (trend_score <= 0).astype(float)
    df["regime_sideways"] = ((trend_score > 0) & (trend_score < 2)).astype(float)
    return df


def build_frame(spec: dict | None = None, use_cache: bool = True) -> tuple[pd.DataFrame, dict]:
    """Returns (labeled frame, resolved spec). Frame columns: FEATURE_COLUMNS
    + label + forward_return + symbol + time + close, chronologically
    ordered with symbols interleaved by time so the chronological
    train/val/test split never trains on one symbol's future to predict
    another's past."""
    spec = {**DEFAULT_SPEC, **(spec or {})}
    symbols = [s.upper() for s in spec["symbols"]]
    timeframe = str(spec["timeframe"])
    bars = int(spec["bars"])
    horizon = max(1, int(spec["horizon"]))
    resolved = {"symbols": symbols, "timeframe": timeframe, "bars": bars, "horizon": horizon}

    cache_path = CACHE_DIR / f"{spec_key(resolved)}.parquet"
    if use_cache and cache_path.exists():
        cached = pd.read_parquet(cache_path)
        # cache is only fresh while its newest bar is recent (2 bars of
        # slack); otherwise rebuild from live history
        newest_ms = float(cached["time"].max())
        interval_ms = float(cached["time"].sort_values().diff().median())
        import time as _time

        if (_time.time() * 1000 - newest_ms) < interval_ms * (horizon + 2):
            return cached, resolved

    parts = []
    for symbol in symbols:
        candles = fetch_klines(symbol, timeframe, bars)
        frame = compute_feature_frame(candles)
        frame["forward_return"] = frame["close"].shift(-horizon) / frame["close"] - 1.0
        frame[LABEL_COLUMN] = (frame["forward_return"] > 0).astype(int)
        frame["symbol"] = symbol
        # drop warmup rows (NaN indicators) and tail rows with no realized
        # forward return yet
        frame = frame.iloc[WARMUP_BARS:]
        frame = frame[frame["forward_return"].notna()]
        parts.append(frame[["symbol", "time", "close", "forward_return", LABEL_COLUMN, *FEATURE_COLUMNS]])

    combined = pd.concat(parts).sort_values("time").reset_index(drop=True)
    if len(combined) < MIN_ROWS:
        raise DatasetUnavailableError(
            f"Only {len(combined)} usable bars after warmup/labeling for "
            f"{symbols}@{timeframe}; need at least {MIN_ROWS}. Increase `bars` or add symbols."
        )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(cache_path, index=False)
    return combined, resolved


def chronological_split(frame: pd.DataFrame, train_frac: float = 0.7, val_frac: float = 0.15):
    n = len(frame)
    train_end = int(n * train_frac)
    val_end = train_end + int(n * val_frac)
    return frame.iloc[:train_end], frame.iloc[train_end:val_end], frame.iloc[val_end:]


def dataset_info(spec: dict | None = None) -> dict:
    """Row counts / class balance / span for the UI dataset panel, without
    building features (single lightweight fetch per symbol)."""
    spec = {**DEFAULT_SPEC, **(spec or {})}
    try:
        frame, resolved = build_frame(spec)
    except (DatasetUnavailableError, httpx.HTTPError) as exc:
        return {"available": False, "reason": str(exc), "spec": spec}

    return {
        "available": True,
        "spec": resolved,
        "rows": len(frame),
        "features": len(FEATURE_COLUMNS),
        "feature_names": FEATURE_COLUMNS,
        "label_balance": round(float(frame[LABEL_COLUMN].mean()), 4),
        "first_bar": int(frame["time"].min()),
        "last_bar": int(frame["time"].max()),
        "symbols": resolved["symbols"],
    }
