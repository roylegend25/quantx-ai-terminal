import time
from datetime import timezone

from fastapi import APIRouter, HTTPException, Query
import httpx
from app.data_sources.downloader import load_candles as load_cached_candles
from app.data_sources.validator import MIN_USABLE_QUALITY
from app.db.models import DataQualityReport, PredictionFeature
from app.db.session import SessionLocal
from app.quant.forecast import build_forecast
from app.quant.indicators import compute_features
from app.trading import risk_manager
from app.trading.risk_manager import calculate_levels
from app.strategy.ensemble import NO_TRADE_BAND as ENSEMBLE_NO_TRADE_BAND
from app.strategy.ensemble import evaluate as ensemble_evaluate
from app.intelligence import market_intelligence
from app.ml_lab import champion_gate
from app.timeframes.multi_timeframe import evaluate_all as evaluate_all_timeframes
from app.ml.feature_store import store as feature_store
from app.monitoring.logging import get_logger, log_event
from app.monitoring.metrics import PREDICTION_LATENCY
from app.monitoring.tracing import span
from app.risk import settings_repository

router = APIRouter(prefix="/api/prediction", tags=["prediction"])

logger = get_logger("quantx.prediction")

BINANCE_FAPI = "https://fapi.binance.com"

# multi-timeframe consensus only ever nudges confidence - it never changes direction
MTF_AGREE_BOOST_MAX = 15.0
MTF_DISAGREE_PENALTY_MAX = 20.0
MTF_NO_TRADE_PENALTY = 10.0

# How long a computed prediction stays valid before the next request triggers a
# recompute. The frontend's "Next Prediction In" countdown mirrors this exact
# window via `computed_at`, so the two must stay in lockstep - see
# frontend/src/components/Dashboard/PredictionGauge.tsx (CYCLE_SECONDS).
PREDICTION_CACHE_TTL_SECONDS = 60
_prediction_cache: dict[tuple[str, str], dict] = {}

# Below this many candles, EMA/RSI/ATR/Bollinger haven't seen enough data to
# mean anything (a cold-started symbol, a gap in history) - the ensemble
# still runs (useful for observability/debugging) but its direction is
# overridden to NO_TRADE rather than acted on.
MIN_CANDLES_FOR_PREDICTION = 50

# Every timeframe the prediction pipeline is expected to serve end-to-end
# (chart timeframe selector, scheduler, research/backtest). An unsupported
# value is rejected outright (400) rather than silently falling back to a
# default interval, so a typo'd or unwired timeframe never masquerades as a
# real (if uninteresting) prediction.
SUPPORTED_INTERVALS_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 3_600_000,
    "4h": 4 * 3_600_000,
    "1d": 86_400_000,
    "1w": 7 * 86_400_000,
}


def _consensus_adjustment(consensus: dict | None, direction: str) -> float:
    if not consensus:
        return 0.0

    if consensus["direction"] == "NO_TRADE":
        return -MTF_NO_TRADE_PENALTY

    agreement = max(0.0, min(100.0, consensus.get("agreement") or 0.0)) / 100

    if consensus["direction"] == direction:
        return round(MTF_AGREE_BOOST_MAX * agreement, 1)

    return round(-MTF_DISAGREE_PENALTY_MAX * agreement, 1)


def make_prediction(
    features: dict,
    market_context: dict | None = None,
    consensus: dict | None = None,
    data_quality: dict | None = None,
):
    ens = ensemble_evaluate(features, market_context)
    decision = ens["ensemble"]

    # ML Champion gate (app/ml_lab/champion_gate.py): the promoted Champion
    # drives the decision only when it is explicitly enabled, exists, loads,
    # and the data-quality verdict is OK - in every other case the strategy
    # ensemble decision above stands untouched. The gate never raises.
    champion_signal = champion_gate.maybe_predict(features, data_quality)
    if champion_signal.get("used"):
        p_up_pct = champion_signal["p_up"] * 100
        # same no-trade band the ensemble uses, so a coin-flip Champion
        # probability doesn't force a direction the ensemble would not
        if abs(p_up_pct - 50) <= ENSEMBLE_NO_TRADE_BAND:
            champion_direction = "NO_TRADE"
        else:
            champion_direction = champion_signal["direction"]
        decision = {
            **decision,
            "direction": champion_direction,
            "confidence": champion_signal["confidence"] if champion_direction != "NO_TRADE" else 0.0,
            "probability_up": round(p_up_pct),
            "probability_down": round(100 - p_up_pct),
        }

    # candle_count is only present when features came from compute_features()
    # (the real pipeline) - a hand-built features dict (unit tests exercising
    # the ensemble directly) omits it, and is treated as "unknown", not
    # "insufficient", so this never fires for those.
    candle_count = features.get("candle_count")
    insufficient_history = candle_count is not None and candle_count < MIN_CANDLES_FOR_PREDICTION
    stale_feed = bool(features.get("stale"))
    # data_quality comes from the endpoint (validated-store quality report +
    # live/cached provenance); an explicitly unreliable dataset is never
    # traded on, same as a stale or too-short one.
    data_unreliable = bool(data_quality) and data_quality.get("reliable") is False
    if insufficient_history or stale_feed or data_unreliable:
        decision = {**decision, "direction": "NO_TRADE", "confidence": 0.0}

    consensus_adjustment = _consensus_adjustment(consensus, decision["direction"])
    confidence = round(max(0.0, min(100.0, decision["confidence"] + consensus_adjustment)), 1)

    price = features["price"]
    atr = features.get("atr") or 1

    levels = calculate_levels(
        price,
        atr,
        decision["direction"],
    )

    # Lightweight, dashboard-editable gate (see app/risk/settings_repository.py):
    # reflects whether this specific direction/confidence would currently
    # clear the risk desk, using the same live-configurable limits the
    # auto-trading scheduler reads. It intentionally doesn't factor in
    # portfolio-level state (daily/weekly loss, drawdown, cooldown) since
    # this endpoint has no portfolio context - that fuller picture is what
    # app.trading.risk_manager.evaluate_risk() computes before the scheduler
    # actually routes an order.
    risk_settings = settings_repository.get_settings()
    required_confidence = round(risk_settings["min_confidence_to_trade"] * 100, 1)
    direction = decision["direction"]
    direction_allowed = (
        (direction == "LONG" and risk_settings["allow_long"])
        or (direction == "SHORT" and risk_settings["allow_short"])
    )

    volume_ok, volume_reason = risk_manager.volume_allowed(features.get("volume"), features.get("volume_sma20"))

    if not risk_settings["paper_trading_enabled"]:
        risk_allowed, risk_reason = False, "Paper trading is disabled in risk settings"
    elif data_unreliable:
        risk_allowed, risk_reason = (
            False,
            data_quality.get("reason") or "Market data quality below the usable threshold - trading blocked",
        )
    elif insufficient_history:
        risk_allowed, risk_reason = (
            False,
            f"Insufficient candle history ({candle_count} < {MIN_CANDLES_FOR_PREDICTION}) for a reliable prediction",
        )
    elif stale_feed:
        risk_allowed, risk_reason = (
            False,
            f"Price feed is stale ({features.get('candle_age_seconds', 0):.0f}s since the last candle) - "
            "waiting for fresh data",
        )
    elif direction == "NO_TRADE":
        risk_allowed, risk_reason = False, "No qualifying trade signal"
    elif not direction_allowed:
        risk_allowed, risk_reason = False, f"{direction.title()} trades disabled by risk settings"
    elif confidence < required_confidence:
        risk_allowed, risk_reason = False, f"Confidence {confidence:.1f}% below required {required_confidence:.1f}%"
    elif not volume_ok:
        risk_allowed, risk_reason = False, volume_reason
    else:
        risk_allowed, risk_reason = True, "Risk checks passed"

    return {
        "direction": decision["direction"],
        "probability_up": decision["probability_up"],
        "probability_down": decision["probability_down"],
        "confidence": confidence,
        "price": round(price, 2),
        "target": levels.take_profit,
        "stop": levels.stop_loss,
        "trailing_stop": levels.trailing_stop,
        "break_even": levels.break_even,
        "regime": ens["regime"],
        "feature_regime": features["regime"],
        "trade_quality": round(confidence / 10, 2),
        "strategies": ens["strategies"],
        "strategy_weights": ens["weights"],
        "market_context": market_context,
        "market_context_adjustment": ens["market_context_adjustment"],
        "multi_timeframe_consensus": consensus,
        "multi_timeframe_adjustment": consensus_adjustment,
        "ml_champion": champion_signal,
        "data_quality": data_quality,
        "risk": {
            "allowed": risk_allowed,
            "reason": risk_reason,
            "required_confidence": required_confidence,
            "max_risk_per_trade_pct": risk_settings["max_risk_per_trade_pct"],
        },
        "features": features,
    }

def _dominant_strategy(weights: dict | None) -> str | None:
    if not weights:
        return None
    return max(weights, key=weights.get)


def _to_epoch_ms(dt) -> int | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def serialize_history_rows(rows: list[PredictionFeature]) -> list[dict]:
    """Turn stored PredictionFeature rows (ascending by time, one
    symbol+timeframe) into chart-ready history points.

    Every value comes from data the system actually recorded: a prediction's
    realized price is the exit price of the paper trade that acted on it if
    one did, otherwise the entry price observed when the *next* prediction in
    the same series was computed. Rows with neither stay PENDING - nothing is
    interpolated or invented.
    """
    out: list[dict] = []
    for i, row in enumerate(rows):
        directional = row.direction in ("LONG", "SHORT")
        predicted_price = row.target if (directional and row.target) else row.entry_price

        actual_price = row.exit_price
        if actual_price is None and i + 1 < len(rows):
            actual_price = rows[i + 1].entry_price

        if row.outcome in ("WIN", "LOSS"):
            outcome = row.outcome
        elif not directional:
            outcome = "NO_TRADE"
        elif actual_price is None or row.entry_price is None:
            outcome = "PENDING"
        else:
            moved_up = actual_price > row.entry_price
            outcome = "CORRECT" if (moved_up == (row.direction == "LONG")) else "INCORRECT"

        error_pct = None
        accuracy_pct = None
        if predicted_price and actual_price:
            error_pct = round(abs(actual_price - predicted_price) / actual_price * 100.0, 2)
            accuracy_pct = round(max(0.0, 100.0 - error_pct), 2)

        out.append(
            {
                "id": row.id,
                "timestamp": _to_epoch_ms(row.timestamp),
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "direction": row.direction,
                "confidence": row.confidence,
                "regime": row.regime,
                "strategy": _dominant_strategy(row.adaptive_strategy_weights),
                "entry_price": row.entry_price,
                "predicted_price": predicted_price,
                "target": row.target,
                "stop": row.stop,
                "actual_price": actual_price,
                # Aliases matching the documented history-record contract
                # (GET /api/prediction/history) - kept alongside the
                # original entry_price/actual_price names above rather than
                # renamed, so existing chart/tooltip code keeps working.
                "actual_price_at_prediction": row.entry_price,
                "actual_price_when_resolved": actual_price,
                "error_pct": error_pct,
                "outcome": outcome,
                "accuracy_pct": accuracy_pct,
                "pnl": row.pnl,
            }
        )
    return out


# registered before /{symbol} so "history" isn't swallowed as a symbol
@router.get("/history")
def prediction_history(
    symbol: str = "BTCUSDT",
    timeframe: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
):
    symbol = symbol.upper()
    db = SessionLocal()
    try:
        query = db.query(PredictionFeature).filter(PredictionFeature.symbol == symbol)
        if timeframe:
            query = query.filter(PredictionFeature.timeframe == timeframe)
        rows = query.order_by(PredictionFeature.timestamp.desc()).limit(limit).all()
    finally:
        db.close()

    rows.reverse()  # chart wants ascending time
    points = serialize_history_rows(rows)

    resolved = [p for p in points if p["outcome"] in ("CORRECT", "INCORRECT", "WIN", "LOSS")]
    hits = [p for p in resolved if p["outcome"] in ("CORRECT", "WIN")]
    accuracies = [p["accuracy_pct"] for p in points if p["accuracy_pct"] is not None]
    confidences = [p["confidence"] for p in points if p["confidence"] is not None]

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "count": len(points),
        "history": points,
        "summary": {
            "resolved": len(resolved),
            "direction_hit_rate_pct": round(len(hits) / len(resolved) * 100, 1) if resolved else None,
            "avg_accuracy_pct": round(sum(accuracies) / len(accuracies), 2) if accuracies else None,
            "avg_confidence": round(sum(confidences) / len(confidences), 1) if confidences else None,
        },
    }


# A cached candle set older than this many intervals is too stale to act on
# even as a fallback - the response then degrades to an explicit NO_TRADE.
CACHE_FRESHNESS_INTERVALS = 3


async def _fetch_candles_with_fallback(symbol: str, interval: str, limit: int) -> tuple[list[dict], dict]:
    """Live Binance klines, falling back to the validated market_candles
    store when the provider is unreachable. Returns (candles, provenance).
    Cached candles are handed to the exact same pipeline - compute_features'
    staleness check and the data-quality gate decide whether they are still
    tradable, so a provider outage can never fabricate a fresh signal."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{BINANCE_FAPI}/fapi/v1/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
            )
            r.raise_for_status()
        candles = [
            {
                "time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            for k in r.json()
        ]
        return candles, {"source": "binance_live", "provider_ok": True, "provider_error": None}
    except (httpx.HTTPError, ValueError) as exc:
        cached = load_cached_candles(symbol, interval, limit=limit)
        return (
            [
                {"time": c["time"], "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": c["volume"]}
                for c in cached
            ],
            {"source": "cached_db", "provider_ok": False, "provider_error": repr(exc)},
        )


def _data_quality_block(symbol: str, interval: str, provenance: dict, candles: list[dict]) -> dict:
    """Reliability verdict for this prediction's input data, combining the
    latest stored quality report with live/cached provenance. `reliable` is
    the single flag the risk gate acts on; `reason` explains a False."""
    db = SessionLocal()
    try:
        report = (
            db.query(DataQualityReport)
            .filter(DataQualityReport.symbol == symbol, DataQualityReport.timeframe == interval)
            .order_by(DataQualityReport.created_at.desc())
            .first()
        )
    finally:
        db.close()

    block = {
        "source": provenance["source"],
        "provider_ok": provenance["provider_ok"],
        "provider_error": provenance["provider_error"],
        "quality_score": report.quality_score if report else None,
        "quality_checked_at": report.created_at.isoformat() if report and report.created_at else None,
        "last_candle_time": candles[-1]["time"] if candles else None,
        "reliable": True,
        "reason": None,
    }

    if not candles:
        block["reliable"] = False
        block["reason"] = "Market data provider unavailable and no cached candles exist - NO_TRADE"
        return block

    if provenance["source"] == "cached_db":
        age_ms = time.time() * 1000 - float(candles[-1]["time"])
        max_age_ms = SUPPORTED_INTERVALS_MS[interval] * CACHE_FRESHNESS_INTERVALS
        if age_ms > max_age_ms:
            block["reliable"] = False
            block["reason"] = (
                f"Provider unavailable and cached {interval} data is {age_ms / 60000:.0f}m old "
                f"(limit {max_age_ms / 60000:.0f}m) - NO_TRADE"
            )
            return block
        # Fresh-enough cache: only trust it if its stored quality clears the bar.
        if report is not None and report.quality_score is not None and report.quality_score < MIN_USABLE_QUALITY:
            block["reliable"] = False
            block["reason"] = (
                f"Cached {symbol} {interval} data quality {report.quality_score:.1f} is below the usable "
                f"threshold {MIN_USABLE_QUALITY} - trading blocked"
            )
    return block


def _no_data_response(symbol: str, interval: str, data_quality: dict) -> dict:
    """Honest degraded response when there is no market data at all: an
    explicit NO_TRADE with empty forecast arrays and the blocking reason -
    nothing fabricated, no invented prices."""
    reason = data_quality["reason"]
    return {
        "symbol": symbol,
        "interval": interval,
        "timeframe": interval,
        "reason": reason,
        "direction": "NO_TRADE",
        "confidence": 0.0,
        "probability_up": None,
        "probability_down": None,
        "current_price": None,
        "target": None,
        "stop": None,
        "forecast_points": [],
        "upper_band_points": [],
        "lower_band_points": [],
        "prediction_horizon": {"bars": 0, "interval": interval, "horizon_ms": 0, "band_basis": None, "reason": reason},
        "prediction": {
            "direction": "NO_TRADE",
            "confidence": 0.0,
            "data_quality": data_quality,
            "risk": {"allowed": False, "reason": reason},
            "computed_at": int(time.time() * 1000),
        },
    }


@router.get("/{symbol}")
async def prediction(symbol: str, interval: str = "5m", timeframe: str | None = None, limit: int = 220):
    symbol = symbol.upper()
    # `timeframe` is accepted as an alias of `interval` (and takes precedence
    # when both are given) so GET /api/prediction/{symbol}?timeframe=1h works
    # the same as ?interval=1h instead of silently falling back to the "5m"
    # default - see PredictionFeature.timeframe / GET /api/prediction/history,
    # which already used "timeframe" as its query param name.
    interval = (timeframe or interval).lower()
    if interval not in SUPPORTED_INTERVALS_MS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported timeframe '{interval}'. Supported: {', '.join(SUPPORTED_INTERVALS_MS)}",
        )
    start = time.perf_counter()

    cache_key = (symbol, interval)
    cached = _prediction_cache.get(cache_key)
    if cached and time.time() - cached["computed_at"] / 1000 < PREDICTION_CACHE_TTL_SECONDS:
        return cached["response"]

    with span("prediction", symbol=symbol, interval=interval):
        candles, provenance = await _fetch_candles_with_fallback(symbol, interval, limit)
        data_quality = _data_quality_block(symbol, interval, provenance, candles)

        if not candles:
            # Provider down and nothing cached: degraded-but-honest NO_TRADE
            # (spec: never fabricate data). Deliberately not cached so the
            # next request retries the provider immediately.
            return _no_data_response(symbol, interval, data_quality)

        features = compute_features(candles)["symbol_features"]

        try:
            market_context = await market_intelligence.get_context(symbol)
        except Exception:
            market_context = None

        try:
            consensus = (await evaluate_all_timeframes(symbol, market_context))["consensus"]
        except Exception:
            consensus = None

        pred = make_prediction(features, market_context, consensus, data_quality=data_quality)
        pred["computed_at"] = int(time.time() * 1000)

        try:
            pred["feature_id"] = feature_store.save_prediction(
                symbol=symbol,
                timeframe=interval,
                prediction=pred,
                # end-to-end latency up to this point (klines fetch +
                # features + ensemble + consensus) - stored per row so the
                # AI Lab inference monitor reports measured latency
                latency_ms=round((time.perf_counter() - start) * 1000, 2),
            )
        except Exception:
            pred["feature_id"] = None

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    PREDICTION_LATENCY.labels(symbol=symbol).observe(latency_ms / 1000)

    weights = pred.get("strategy_weights") or {}
    dominant_strategy = max(weights, key=weights.get) if weights else None

    log_event(
        logger,
        message="prediction_generated",
        category="prediction",
        endpoint=f"/api/prediction/{symbol}",
        prediction_id=pred.get("feature_id"),
        strategy=dominant_strategy,
        confidence=pred.get("confidence"),
        latency_ms=latency_ms,
        symbol=symbol,
        error=None,
    )

    # Server-side forecast + confidence-band points, anchored to the last
    # closed candle. The chart draws exactly these; a NO_TRADE prediction
    # gets empty arrays plus the reason - nothing fabricated. Also mirrored
    # into `prediction` so consumers reading either level see them.
    forecast = build_forecast(
        interval=interval,
        interval_ms=SUPPORTED_INTERVALS_MS[interval],
        last_candle_time=candles[-1]["time"] if candles else 0,
        price=pred["price"],
        direction=pred["direction"],
        confidence=pred["confidence"],
        target=pred["target"],
        stop=pred["stop"],
        atr=features.get("atr"),
        realized_volatility=features.get("realized_volatility"),
    )
    pred["forecast_points"] = forecast["forecast_points"]
    pred["upper_band_points"] = forecast["upper_band_points"]
    pred["lower_band_points"] = forecast["lower_band_points"]
    pred["forecast"] = {
        "bars": forecast["bars"],
        "horizon_ms": forecast["horizon_ms"],
        "band_basis": forecast["band_basis"],
        "reason": forecast["reason"],
    }

    response = {
        "symbol": symbol,
        "interval": interval,
        # Convenience top-level mirrors of fields already nested in
        # `prediction` (kept, unchanged, for existing consumers) - added so
        # every timeframe/symbol combination exposes the same documented
        # top-level contract without a client having to know which fields
        # live under `prediction`.
        "timeframe": interval,
        "reason": pred["risk"]["reason"],
        "direction": pred["direction"],
        "confidence": pred["confidence"],
        "probability_up": pred["probability_up"],
        "probability_down": pred["probability_down"],
        "current_price": pred["price"],
        "target": pred["target"],
        "stop": pred["stop"],
        "data_quality": data_quality,
        "forecast_points": forecast["forecast_points"],
        "upper_band_points": forecast["upper_band_points"],
        "lower_band_points": forecast["lower_band_points"],
        "prediction_horizon": {
            "bars": forecast["bars"],
            "interval": interval,
            "horizon_ms": forecast["horizon_ms"],
            "band_basis": forecast["band_basis"],
            "reason": forecast["reason"],
        },
        "prediction": pred,
    }
    _prediction_cache[cache_key] = {"computed_at": pred["computed_at"], "response": response}
    return response
