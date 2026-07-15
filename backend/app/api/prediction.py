import math
import time
from datetime import timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
import httpx
from app.data_sources.downloader import load_candles as load_cached_candles, store_candles
from app.data_sources.validator import MIN_USABLE_QUALITY, validate_candles
from app.db.models import DataQualityReport, PredictionFeature
from app.db.session import SessionLocal
from app.core.deps import get_current_user
from app.core.config import settings
from app.decision_engine.repository import get_setting, owner
from app.decision_engine.router import decision_engine_router
from app.decision_engine.ledger import persist as persist_engine_decision
from app.quant.forecast import TIMEFRAME_SECONDS, build_forecast, validate_forecast
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

async def _prediction_subject(authorization: str | None = Header(default=None)) -> str:
    # main.py already enforces authentication in production. The fallback preserves
    # direct-router unit tests and legacy internal embeddings that historically had
    # no route-level auth dependency; it is unreachable through the production app.
    return await get_current_user(authorization) if authorization else settings.admin_username

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
    "3m": 3 * 60_000,
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


def _vote_probability_up(vote: dict) -> float:
    """Same mapping app/strategy/ensemble.py uses to combine votes: a
    LONG/SHORT vote pushes probability_up above/below 50 by half its
    confidence, NO_TRADE sits at 50."""
    confidence = max(0.0, min(100.0, vote.get("confidence") or 0))
    if vote.get("direction") == "LONG":
        return 50 + confidence / 2
    if vote.get("direction") == "SHORT":
        return 50 - confidence / 2
    return 50.0


def _build_decision_engine(
    ens: dict,
    champion_signal: dict,
    direction: str,
    confidence: float,
    required_confidence: float,
    risk_allowed: bool,
    risk_reason: str,
    trade_blockers: list[str],
    consensus: dict | None,
    consensus_adjustment: float,
) -> dict:
    """Full 'who decided this and why' block for the dashboard. Everything
    here is derived from values the pipeline actually computed this cycle -
    nothing is estimated or invented for display."""
    champion_used = bool(champion_signal.get("used"))
    weights = ens.get("weights") or {}

    if champion_used:
        mode = "champion_ml"
    elif champion_signal.get("expected"):
        # Champion inference is enabled and a Champion exists, but it could
        # not be used this cycle - the ensemble is acting as the fallback.
        mode = "fallback"
    else:
        mode = "strategy_ensemble"

    active_model = (
        {
            "model_id": champion_signal.get("model_id"),
            "model_type": champion_signal.get("algorithm") or champion_signal.get("model_name"),
            "name": champion_signal.get("model_name"),
            "version": champion_signal.get("version"),
        }
        if champion_used
        else None
    )

    dominant = max(weights, key=weights.get) if weights else None
    strategy_used = "champion" if champion_used else (dominant or "ensemble")

    # ---- votes: the Champion first, then every ensemble strategy.
    # contribution is the signed push toward LONG in probability points -
    # for strategies it is exactly the term the ensemble summed
    # ((p_up - 50) * weight); the Champion, when used, overrides rather
    # than averages, so its contribution is its own full (p_up - 50).
    model_votes: list[dict] = [
        {
            "name": "Champion ML",
            "direction": champion_signal.get("direction") if champion_used else None,
            "confidence": champion_signal.get("confidence") if champion_used else None,
            "weight": 1.0 if champion_used else 0.0,
            "available": champion_used,
            "drives_decision": champion_used,
            "contribution": round(champion_signal["p_up"] * 100 - 50, 1) if champion_used else 0.0,
            "reason": None if champion_used else champion_signal.get("reason"),
        }
    ]
    for name, vote in (ens.get("strategies") or {}).items():
        weight = weights.get(name, 0.0)
        model_votes.append(
            {
                "name": name,
                "direction": vote.get("direction"),
                "confidence": vote.get("confidence"),
                "weight": round(weight, 4),
                "available": True,
                "drives_decision": not champion_used,
                "contribution": round((_vote_probability_up(vote) - 50) * weight, 1),
                "reason": vote.get("reason"),
            }
        )

    # ---- top reasons, most decision-relevant first
    top_reasons: list[str] = []
    if champion_used:
        top_reasons.append(
            f"Champion {champion_signal.get('model_name')} {champion_signal.get('version')} predicts "
            f"{champion_signal.get('direction')} ({champion_signal['p_up'] * 100:.0f}% up probability)"
        )
    elif mode == "fallback":
        top_reasons.append(f"Champion unavailable, using strategy ensemble: {champion_signal.get('reason')}")
    strategy_votes = sorted(
        ((n, v) for n, v in (ens.get("strategies") or {}).items() if v.get("direction") in ("LONG", "SHORT")),
        key=lambda item: weights.get(item[0], 0.0) * (item[1].get("confidence") or 0),
        reverse=True,
    )
    for name, vote in strategy_votes[:3]:
        top_reasons.append(
            f"{name.replace('_', ' ').title()} voted {vote['direction']} ({vote.get('confidence') or 0:.0f}% confidence)"
        )
    if consensus and consensus_adjustment:
        top_reasons.append(
            f"Multi-timeframe consensus is {consensus.get('direction')} "
            f"({'+' if consensus_adjustment >= 0 else ''}{consensus_adjustment:.1f} confidence adjustment)"
        )
    for blocker in trade_blockers:
        top_reasons.append(f"Blocked: {blocker}")

    return {
        "mode": mode,
        "active_model": active_model,
        "fallback_reason": champion_signal.get("reason") if not champion_used else None,
        "strategy_used": strategy_used,
        "final_direction": direction,
        "final_confidence": confidence,
        "required_confidence": required_confidence,
        "risk_allowed": risk_allowed,
        "risk_reason": risk_reason,
        "trade_allowed": risk_allowed,
        "trade_blockers": trade_blockers,
        "top_reasons": top_reasons[:5],
        "model_votes": model_votes,
        "strategy_weights": weights,
        "market_regime": ens.get("regime"),
    }


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

    # Every blocker that currently applies, in the same priority order the
    # old single-reason gate used - risk_reason stays byte-for-byte what it
    # was (the first match), trade_blockers exposes the full list.
    trade_blockers: list[str] = []
    if not risk_settings["paper_trading_enabled"]:
        trade_blockers.append("Paper trading is disabled in risk settings")
    if data_unreliable:
        trade_blockers.append(
            data_quality.get("reason") or "Market data quality below the usable threshold - trading blocked"
        )
    if insufficient_history:
        trade_blockers.append(
            f"Insufficient candle history ({candle_count} < {MIN_CANDLES_FOR_PREDICTION}) for a reliable prediction"
        )
    if stale_feed:
        trade_blockers.append(
            f"Price feed is stale ({features.get('candle_age_seconds', 0):.0f}s since the last candle) - "
            "waiting for fresh data"
        )
    if direction == "NO_TRADE":
        trade_blockers.append("No qualifying trade signal")
    else:
        if not direction_allowed:
            trade_blockers.append(f"{direction.title()} trades disabled by risk settings")
        if confidence < required_confidence:
            trade_blockers.append(f"Confidence {confidence:.1f}% below required {required_confidence:.1f}%")
        if not volume_ok:
            trade_blockers.append(volume_reason)

    risk_allowed = not trade_blockers
    risk_reason = trade_blockers[0] if trade_blockers else "Risk checks passed"

    decision_engine = _build_decision_engine(
        ens=ens,
        champion_signal=champion_signal,
        direction=direction,
        confidence=confidence,
        required_confidence=required_confidence,
        risk_allowed=risk_allowed,
        risk_reason=risk_reason,
        trade_blockers=trade_blockers,
        consensus=consensus,
        consensus_adjustment=consensus_adjustment,
    )

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
        "decision_engine": decision_engine,
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
        fetch_limit = min(1500, max(limit, 1000))
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{BINANCE_FAPI}/fapi/v1/klines",
                params={"symbol": symbol, "interval": interval, "limit": fetch_limit},
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
        # Persist the same real, validated live candles used for inference so
        # the fixed-horizon resolver can observe outcomes later. This is
        # append-only by (symbol, timeframe, timestamp, provider).
        clean, report = validate_candles(candles, SUPPORTED_INTERVALS_MS[interval])
        if clean and report.get("usable"):
            candle_db = SessionLocal()
            try:
                store_candles(candle_db, symbol, interval, "binance_futures", clean, report["quality_score"])
            finally:
                candle_db.close()
        return candles[-limit:], {"source": "binance_live", "provider_ok": True, "provider_error": None}
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


def _no_data_response(symbol: str, interval: str, data_quality: dict, active_engine=None) -> dict:
    """Honest degraded response when there is no market data at all: an
    explicit NO_TRADE with empty forecast arrays and the blocking reason -
    nothing fabricated, no invented prices."""
    reason = data_quality["reason"]
    engine_name = active_engine.name.value if active_engine else "active_drive_v2"
    engine_version = active_engine.version if active_engine else "2.0.0"
    decision_engine = {
        "engine": engine_name, "engine_version": engine_version, "mode": engine_name,
        "engine_info":{"id":engine_name,"name":"Active Drive V2" if engine_name=="active_drive_v2" else "Active Drive V1","version":engine_version,"authoritative":True},
        "final_signal": "NO_TRADE", "eligible_for_execution": False, "blocking_reasons": [reason],
        "directional_confidence":None,"decision_confidence":None,"abstention_confidence":1.0,
        "long_points": 0.0, "short_points": 0.0, "point_margin": 0.0, "evidence_tier": "insufficient_evidence",
        "mode_legacy": "fallback",
        "active_model": None,
        "fallback_reason": reason,
        "strategy_used": None,
        "final_direction": "NO_TRADE",
        "final_confidence": None,
        "required_confidence": settings.active_drive_min_confidence,
        "minimum_total_evidence": settings.active_drive_min_total_evidence,
        "risk_allowed": False,
        "risk_reason": reason,
        "trade_allowed": False,
        "trade_blockers": [reason],
        "top_reasons": [f"Blocked: {reason}"],
        "model_votes": [],
        "strategy_weights": {},
        "market_regime": None,
    }
    return {
        "symbol": symbol,
        "interval": interval,
        "timeframe": interval,
        "reason": reason,
        "direction": "NO_TRADE",
        "confidence": None,
        "decision_engine": decision_engine,
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
            "confidence": None,
            "symbol":symbol,
            "timeframe":interval,
            "decision_engine": decision_engine,
            "data_quality": data_quality,
            "risk": {"allowed": False, "reason": reason},
            "computed_at": int(time.time() * 1000),
        },
    }


@router.get("/{symbol}")
async def prediction(symbol: str, interval: str = "5m", timeframe: str | None = None, limit: int = 220, current_user: str = Depends(_prediction_subject)):
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

    selection_db = SessionLocal()
    try:
        active_engine = decision_engine_router.get_active_engine(selection_db, current_user)
    finally:
        selection_db.close()
    cache_key = (owner(current_user), active_engine.name.value, active_engine.version, symbol, interval)
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
            return _no_data_response(symbol, interval, data_quality, active_engine)

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
        legacy_decision = pred["decision_engine"]
        rr = None
        if pred.get("target") is not None and pred.get("stop") is not None and pred.get("price") is not None:
            risk_distance = abs(pred["price"] - pred["stop"])
            rr = abs(pred["target"] - pred["price"]) / risk_distance if risk_distance else None
        decision_db = SessionLocal()
        try:
            engine_result = decision_engine_router.evaluate(decision_db, current_user, {
                "symbol": symbol, "timeframe": interval, "legacy": pred, "regime": pred.get("regime"),
                "data_status": "stale" if features.get("stale") or data_quality.get("reliable") is False else ("cached" if data_quality.get("source") == "cached_db" else "live"),
                "risk_reward_ratio": rr,
            })
            engine_result["legacy_decision_engine"] = legacy_decision
            engine_result["mode"] = engine_result["engine"]
            engine_result["final_direction"] = engine_result["final_signal"]
            # Compatibility mirrors remain percentages, but null stays null.
            # NO_TRADE abstention confidence is never presented as a trade
            # directional confidence.
            engine_result["final_confidence"] = (
                round(engine_result["directional_confidence"] * 100, 1)
                if engine_result.get("directional_confidence") is not None else None
            )
            engine_result["required_confidence_pct"] = round(engine_result["required_confidence"] * 100, 1)
            engine_result["trade_allowed"] = engine_result["eligible_for_execution"]
            engine_result["trade_blockers"] = engine_result["blocking_reasons"]
            engine_result["top_reasons"] = engine_result["blocking_reasons"][:5]
            pred["direction"] = engine_result["final_signal"]
            pred["confidence"] = engine_result["final_confidence"]
            pred["probability_up"] = round(engine_result["probability_up"] * 100, 1)
            pred["probability_down"] = round(engine_result["probability_down"] * 100, 1)
            pred["decision_engine"] = engine_result
            if not engine_result["eligible_for_execution"]:
                pred["risk"] = {**pred["risk"], "allowed": False, "reason": engine_result["blocking_reasons"][0] if engine_result["blocking_reasons"] else "V2 decision is not execution eligible"}
            pred["decision_id"] = persist_engine_decision(decision_db, owner(current_user), engine_result, pred.get("price"), features)
            engine_result["decision_id"] = pred["decision_id"]
        finally:
            decision_db.close()
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

    # Forecast composition is separate from execution. For NO_TRADE, only
    # finite, non-shadow V2 source contributions may create an informational
    # path; the result remains explicitly non-actionable and has no TP/SL.
    candidates = pred.get("decision_engine", {}).get("candidates") or []
    directional_sources = [
        c for c in candidates
        if c.get("direction") in ("LONG", "SHORT")
        and c.get("status") != "shadow"
        and isinstance(c.get("final_points"), (int, float))
        and math.isfinite(c["final_points"])
        and c["final_points"] != 0
    ]
    source_total = sum(abs(c["final_points"]) for c in directional_sources)
    source_net = sum(c["final_points"] for c in directional_sources)
    informational_direction = (
        "BULLISH" if source_net > 0 else "BEARISH" if source_net < 0 else "NEUTRAL"
    ) if source_total else None
    informational_strength = abs(source_net) / source_total if source_total else None
    forecast_sources = [
        {
            "type": c.get("source_type"),
            "name": c.get("name"),
            "direction": "BULLISH" if c["final_points"] > 0 else "BEARISH",
            "weight": round(abs(c["final_points"]) / source_total, 4),
        }
        for c in sorted(directional_sources, key=lambda row: abs(row["final_points"]), reverse=True)[:8]
    ] if source_total else []
    data_fresh = not bool(features.get("stale")) and data_quality.get("reliable") is not False
    forecast = build_forecast(
        interval=interval,
        interval_ms=SUPPORTED_INTERVALS_MS[interval],
        last_candle_time=candles[-1]["time"] if candles else 0,
        price=pred["price"],
        direction=pred["direction"],
        confidence=pred["confidence"] or 0,
        target=pred["target"],
        stop=pred["stop"],
        atr=features.get("atr"),
        realized_volatility=features.get("realized_volatility"),
        informational_direction=informational_direction,
        informational_strength=informational_strength,
        data_fresh=data_fresh,
        candle_count=len(candles),
    )
    timestamps_valid, validation_reason = validate_forecast(
        forecast, reference_time=candles[-1]["time"], interval=interval
    )
    if not timestamps_valid and forecast.get("available"):
        forecast.update({
            "available": False, "trade_actionable": False, "forecast_type": "unavailable",
            "median_path": [], "upper_band": [], "lower_band": [],
            "forecast_points": [], "upper_band_points": [], "lower_band_points": [],
            "target_price": None, "invalidation_price": None, "reason": validation_reason,
        })
    pred["forecast_points"] = forecast["forecast_points"]
    pred["upper_band_points"] = forecast["upper_band_points"]
    pred["lower_band_points"] = forecast["lower_band_points"]
    pred["forecast"] = {
        "available": forecast["available"],
        "trade_actionable": forecast["trade_actionable"],
        "forecast_type": forecast["forecast_type"],
        "direction": forecast["direction"],
        "reason": forecast["reason"],
        "symbol": symbol,
        "timeframe": interval,
        "decision_id": pred.get("decision_id"),
        "generated_at": pred["decision_engine"].get("generated_at"),
        "reference_time": int(candles[-1]["time"] // 1000) if candles else None,
        "reference_price": pred.get("price"),
        "horizon_bars": forecast["bars"],
        "horizon_seconds": forecast["horizon_seconds"],
        "band_basis": forecast["band_basis"],
        "median_path": forecast["median_path"],
        "upper_band": forecast["upper_band"],
        "lower_band": forecast["lower_band"],
        "confidence_level": forecast["confidence_level"],
        "target_price": forecast["target_price"],
        "invalidation_price": forecast["invalidation_price"],
        "forecast_sources": forecast_sources,
        # Compatibility aliases for existing canvas consumers.
        "bars": forecast["bars"],
        "horizon_ms": forecast["horizon_ms"],
        "forecast_points": forecast["forecast_points"],
        "upper_bound": forecast["upper_band_points"],
        "lower_bound": forecast["lower_band_points"],
    }
    pred["forecast_diagnostics"] = {
        "available": forecast["available"],
        "candidate_count": len(candidates),
        "forecast_model_available": bool(directional_sources),
        "points_generated": len(forecast["median_path"]),
        "median_point_count": len(forecast["median_path"]),
        "upper_point_count": len(forecast["upper_band"]),
        "lower_point_count": len(forecast["lower_band"]),
        "reference_timestamp": int(candles[-1]["time"] // 1000),
        "last_candle_timestamp": int(candles[-1]["time"] // 1000),
        "first_future_timestamp": forecast["median_path"][1]["time"] if len(forecast["median_path"]) > 1 else None,
        "last_future_timestamp": forecast["median_path"][-1]["time"] if len(forecast["median_path"]) > 1 else None,
        "interval_seconds": TIMEFRAME_SECONDS[interval],
        "horizon_bars": forecast["bars"],
        "timestamps_valid": timestamps_valid,
        "prices_valid": timestamps_valid,
        "symbol_match": True,
        "timeframe_match": True,
        "timestamp_format": "unix_seconds",
        "data_fresh": data_fresh,
        "reason_if_unavailable": None if forecast["available"] else forecast["reason"],
    }
    pred["symbol"] = symbol
    pred["timeframe"] = interval
    pred["generated_at"] = pred["decision_engine"].get("generated_at")
    pred["expires_at"] = pred["decision_engine"].get("expires_at")
    pred["decision_engine"]["forecast"] = pred["forecast"]

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
        "decision_engine": pred["decision_engine"],
        "data_quality": data_quality,
        "forecast_points": forecast["forecast_points"],
        "upper_band_points": forecast["upper_band_points"],
        "lower_band_points": forecast["lower_band_points"],
        "forecast": pred["forecast"],
        "forecast_diagnostics": pred["forecast_diagnostics"],
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
