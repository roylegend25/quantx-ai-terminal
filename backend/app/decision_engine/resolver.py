"""Resilient prediction-resolution worker (Phase 33).

Two passes, both idempotent and safe to call every cycle:

  backfill_overdue_candles() - async. For due-but-unresolved rows whose
  required candle isn't already in the local MarketCandle cache, attempts
  ONE bounded fetch from the approved primary provider (app.data_sources.
  binance_futures, the same public Binance klines adapter every other
  candle-fetch path in this app already uses), with exponential backoff
  between attempts so a genuine gap doesn't get hammered every cycle.

  resolve_due() - sync (unchanged interface - existing callers/tests use it
  synchronously). Resolves only after resolution_deadline has genuinely
  elapsed, using exchange-timestamped candles already in the local cache -
  never the current price as a horizon-price substitute, never early.
  Every row that can't resolve this cycle gets a structured, persisted
  unresolved_reason instead of silently vanishing from the queue.

The scheduler (app.decision_engine.scheduler) runs backfill_overdue_candles
first, then resolve_due, every cycle.
"""

from datetime import datetime, timedelta, timezone

from app.db.models import MarketCandle, PredictionLedger, PredictionResolution
from app.core.config import settings
from app.monitoring.logging import get_logger, log_event

logger = get_logger("quantx.active_drive_resolver")

# Canonical timeframe axis this resolver (and the rest of Active Drive V2)
# supports. A row outside this set is honestly unresolvable, never retried.
SUPPORTED_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"}

# After this many failed backfill attempts AND this much elapsed time, a gap
# is classified permanent rather than retried forever - most real provider
# gaps resolve within a few attempts; one that hasn't after days of retries
# at capped backoff is a genuine, permanent hole (e.g. corrupted legacy
# due-time metadata, a symbol/timeframe combination that never traded).
MAX_RETRY_ATTEMPTS_BEFORE_PERMANENT = 8
PERMANENT_GAP_MIN_AGE = timedelta(days=3)

# Exponential backoff between backfill attempts for one row: 30s, 60s,
# 120s, ... capped at 1h so a real provider outage is retried steadily
# without ever going silent for longer than an hour.
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600


def _backoff_seconds(attempts: int) -> int:
    return min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)))


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite round-trips DateTime columns as naive even though every write
    here uses timezone-aware UTC values - treat a naive result as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _first_candle_at_or_after(db, symbol: str, timeframe: str, deadline_ms: int) -> MarketCandle | None:
    return (
        db.query(MarketCandle)
        .filter(MarketCandle.symbol == symbol, MarketCandle.timeframe == timeframe, MarketCandle.timestamp >= deadline_ms)
        .order_by(MarketCandle.timestamp)
        .first()
    )


def _due_unresolved_query(db, now: datetime):
    return (
        db.query(PredictionLedger)
        .outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(PredictionResolution.id.is_(None), PredictionLedger.resolution_deadline <= now)
        .order_by(PredictionLedger.resolution_deadline)
    )


_FALLBACK_ORDER = ["bybit", "okx", "hyperliquid"]


async def _try_multi_exchange_fallback(db, row: PredictionLedger, at_ms: int) -> tuple[bool, str | None]:
    """Only reached once the primary provider (Binance) has a verified gap
    for this exact row. Scoped to symbol_map.CANONICAL_SYMBOLS (BTCUSDT/
    ETHUSDT today) - every other symbol never makes a fallback network call,
    so this never changes behavior for anything outside this feature's
    stated scope. Requires 2 independent providers to agree within
    settings.resolver_price_disagreement_bps before storing anything; a
    lone reachable provider is accepted at a discounted confidence rather
    than leaving a real gap unresolved forever."""
    from app.data_sources import resolution_providers as providers
    from app.data_sources import symbol_map
    from app.data_sources.downloader import store_candles
    from app.core.config import settings

    if not symbol_map.supported(row.symbol) or row.timeframe == "1M":
        return False, None  # no fallback available - caller keeps its own primary-failure reason

    fallback_names = list(_FALLBACK_ORDER)
    if settings.resolver_allow_spot_fallback:
        fallback_names.append("binance_spot")

    observations = []
    for name in fallback_names:
        obs = await providers.PROVIDER_FETCHERS[name](row.symbol, row.timeframe, at_ms)
        if obs.ok:
            observations.append(obs)
        if len(observations) >= 2:
            break

    if not observations:
        return False, None

    if len(observations) == 1:
        obs = observations[0]
        confidence = obs.confidence * 0.7  # single independent source - discounted
    else:
        a, b = observations[0], observations[1]
        mid = (a.price + b.price) / 2
        spread_bps = abs(a.price - b.price) / mid * 10_000 if mid else None
        if spread_bps is not None and spread_bps > settings.resolver_price_disagreement_bps:
            log_event(logger, message="prediction_resolver_provider_disagreement", category="prediction",
                      prediction_id=row.prediction_id, symbol=row.symbol, providers=[a.provider, b.provider], spread_bps=spread_bps)
            return False, "provider_disagreement"
        obs = providers.ResolutionPriceObservation(
            provider=f"{a.provider}+{b.provider}", exchange=f"{a.exchange}+{b.exchange}", market_type=a.market_type,
            symbol=row.symbol, requested_timestamp=at_ms, actual_market_timestamp=a.actual_market_timestamp,
            price=mid, confidence=min(a.confidence, b.confidence))
        confidence = obs.confidence

    store_candles(db, row.symbol, row.timeframe, observations[0].provider,
                  [{"time": obs.actual_market_timestamp, "open": obs.price, "high": obs.price,
                    "low": obs.price, "close": obs.price, "volume": 0.0}], quality=confidence * 100.0)
    return True, None


async def _fetch_and_store_backfill(db, row: PredictionLedger) -> tuple[bool, str | None]:
    """One bounded fetch from the approved primary provider for the exact
    window the resolution needs. Never a current-price substitute - this
    only ever requests historical candles at/around resolution_deadline.
    Falls back to a small multi-exchange chain only once the primary
    provider has a verified gap for this exact row (see
    _try_multi_exchange_fallback)."""
    from app.data_sources.binance_futures import fetch_klines
    from app.data_sources.downloader import store_candles
    from app.data_sources.normalizer import normalize_klines
    from app.timeframes.canonical import to_provider_interval

    try:
        provider_interval = to_provider_interval(row.timeframe, "binance_futures")
    except Exception as e:
        return False, f"unsupported_timeframe: {e}"

    deadline = _aware(row.resolution_deadline)
    start_ms = int(deadline.timestamp() * 1000)
    # A small forward window is enough to find "the first candle at/after
    # the deadline" - this is a targeted backfill of the exact missing
    # observation, not a bulk historical download.
    end_ms = start_ms + 6 * 3_600_000

    try:
        raw = await fetch_klines(row.symbol, provider_interval, start_ms=start_ms, end_ms=end_ms, limit=12)
    except Exception as e:
        raw = None
        primary_error = f"{type(e).__name__}: could not reach provider"
    else:
        primary_error = None if raw else "provider returned no candles for the requested window"

    if raw:
        try:
            rows = normalize_klines(raw)
            store_candles(db, row.symbol, row.timeframe, "binance_futures", rows, quality=100.0)
        except Exception as e:
            return False, f"{type(e).__name__}: failed to store fetched candles"
        return True, None

    fallback_ok, fallback_error = await _try_multi_exchange_fallback(db, row, start_ms)
    if fallback_ok:
        return True, None
    return False, fallback_error or primary_error


def _classify_missing_candle_reason(error: str | None) -> str:
    if error == "provider_disagreement":
        return "exchange_price_disagreement"
    if error and "provider returned no candles" in error:
        return "market_data_gap"
    if error and "unsupported_timeframe" in error:
        return "unsupported_timeframe"
    return "provider_unavailable"


async def backfill_overdue_candles(db, limit: int = 50) -> int:
    """Async catch-up pass: attempts to fetch the missing candle for up to
    `limit` due-but-gapped rows this cycle. Never resolves anything itself -
    only populates MarketCandle so the sync resolve_due() pass can. Returns
    the number of rows that successfully got a usable candle stored."""
    now = datetime.now(timezone.utc)
    rows = _due_unresolved_query(db, now).limit(max(2000, limit * 25)).all()

    backfilled = 0
    attempted = 0
    for row in rows:
        if attempted >= limit:
            break
        if row.reference_price is None or row.resolution_deadline is None:
            continue  # permanently unresolvable - resolve_due classifies these, no backfill possible
        if row.timeframe not in SUPPORTED_TIMEFRAMES:
            continue

        deadline = _aware(row.resolution_deadline)
        deadline_ms = int(deadline.timestamp() * 1000)
        candle = _first_candle_at_or_after(db, row.symbol, row.timeframe, deadline_ms)
        if candle and candle.close:
            continue  # already resolvable, nothing to backfill

        next_retry = _aware(row.next_retry_at)
        if next_retry is not None and next_retry > now:
            continue  # respecting exponential backoff - not due for another attempt yet

        attempted += 1
        attempts = (row.resolver_attempts or 0) + 1
        generated = _aware(row.generated_at) or now
        age = now - generated
        if attempts > MAX_RETRY_ATTEMPTS_BEFORE_PERMANENT and age > PERMANENT_GAP_MIN_AGE:
            row.unresolved_reason = "permanent_data_gap"
            row.resolver_attempts = attempts
            row.last_resolver_attempt_at = now
            log_event(logger, message="prediction_permanent_data_gap", category="prediction",
                      prediction_id=row.prediction_id, symbol=row.symbol, timeframe=row.timeframe, attempts=attempts)
            continue

        try:
            ok, error = await _fetch_and_store_backfill(db, row)
        except Exception as e:
            ok, error = False, f"{type(e).__name__}: resolver error during backfill"
            row.unresolved_reason = "resolver_error"
            row.resolver_attempts = attempts
            row.last_resolver_attempt_at = now
            row.last_resolver_error = error
            row.next_retry_at = now + timedelta(seconds=_backoff_seconds(attempts))
            continue

        row.resolver_attempts = attempts
        row.last_resolver_attempt_at = now
        if ok:
            row.last_resolver_error = None
            backfilled += 1
        else:
            row.last_resolver_error = error
            row.next_retry_at = now + timedelta(seconds=_backoff_seconds(attempts))
            row.unresolved_reason = _classify_missing_candle_reason(error)

    db.commit()
    return backfilled


def resolve_due(db, limit: int = 200, scan_limit: int | None = None) -> int:
    """Resolves up to `limit` due predictions this cycle from whatever is
    already in the local MarketCandle cache (run backfill_overdue_candles
    first to give genuinely gapped rows a chance). Idempotent (guarded by
    the outer join on PredictionResolution) and safe to call from a
    single-instance scheduler every cycle."""
    now = datetime.now(timezone.utc)
    # Legacy gaps must not permanently starve later resolvable predictions.
    # Inspect a bounded superset, while limiting successful writes per cycle.
    scan_limit = scan_limit or max(5000, limit * 25)
    rows = _due_unresolved_query(db, now).limit(scan_limit).all()

    resolved = 0
    for row in rows:
        if resolved >= limit:
            break

        # ---- permanently unresolvable classifications: never retried ----
        if row.reference_price is None:
            row.unresolved_reason = "missing_entry_price"
            continue
        if row.resolution_deadline is None:
            row.unresolved_reason = "invalid_due_time"
            continue
        if row.timeframe not in SUPPORTED_TIMEFRAMES:
            row.unresolved_reason = "unsupported_timeframe"
            continue

        deadline = _aware(row.resolution_deadline)
        deadline_ms = int(deadline.timestamp() * 1000)
        candle = _first_candle_at_or_after(db, row.symbol, row.timeframe, deadline_ms)
        if not candle or not candle.close:
            # Nothing in the cache yet - leave whatever reason the last
            # backfill_overdue_candles pass set (market_data_gap,
            # provider_unavailable, permanent_data_gap, resolver_error), or
            # fall back to a generic honest label if a backfill hasn't run
            # against this row yet at all.
            if not row.unresolved_reason:
                row.unresolved_reason = "awaiting_future_candle"
            elif row.next_retry_at is not None and _aware(row.next_retry_at) > now:
                row.unresolved_reason = "resolver_delayed"
            continue

        # ---- resolution math ----
        actual_return = (float(candle.close) - row.reference_price) / row.reference_price
        path = db.query(MarketCandle).filter(
            MarketCandle.symbol == row.symbol, MarketCandle.timeframe == row.timeframe,
            MarketCandle.timestamp >= int(_aware(row.generated_at).timestamp() * 1000),
            MarketCandle.timestamp <= deadline_ms,
        ).order_by(MarketCandle.timestamp).all()
        highs = [float(item.high) for item in path if item.high is not None]
        lows = [float(item.low) for item in path if item.low is not None]
        if row.direction == "LONG":
            mfe = (max(highs) - row.reference_price) / row.reference_price if highs else None
            mae = (min(lows) - row.reference_price) / row.reference_price if lows else None
            target_hit = bool(row.target_reference_price is not None and highs and max(highs) >= row.target_reference_price)
            stop_hit = bool(row.stop_reference_price is not None and lows and min(lows) <= row.stop_reference_price)
        elif row.direction == "SHORT":
            mfe = (row.reference_price - min(lows)) / row.reference_price if lows else None
            mae = (row.reference_price - max(highs)) / row.reference_price if highs else None
            target_hit = bool(row.target_reference_price is not None and lows and min(lows) <= row.target_reference_price)
            stop_hit = bool(row.stop_reference_price is not None and highs and max(highs) >= row.stop_reference_price)
        else:
            mfe = mae = None
            target_hit = stop_hit = None

        # Symmetric neutral band (see settings.resolution_neutral_band): a
        # move whose magnitude never clears the band is NEUTRAL for both
        # LONG and SHORT predictions, and neutral outcomes stay out of the
        # directional-accuracy denominator (correct stays NULL) instead of
        # counting a sub-noise move as a win or a loss.
        band = abs(settings.resolution_neutral_band)
        actual_direction = "NEUTRAL" if abs(actual_return) <= band else "LONG" if actual_return > 0 else "SHORT"
        correct = None if row.direction not in ("LONG", "SHORT") or actual_direction == "NEUTRAL" else row.direction == actual_direction

        fallback_used = candle.provider != "binance_futures"
        db.add(PredictionResolution(
            prediction_id=row.prediction_id, actual_return=actual_return, resolved_direction=actual_direction,
            correct=correct, neutral_result=actual_direction == "NEUTRAL", target_hit=target_hit, stop_hit=stop_hit,
            maximum_favorable_excursion=mfe, maximum_adverse_excursion=mae,
            resolution_reason="fixed_horizon_close", resolved_at=now,
            resolution_provider=candle.provider, resolution_exchange="binance" if not fallback_used else candle.provider.split("+")[0],
            resolution_market_type="usdt_perp", resolved_market_timestamp=candle.timestamp, resolved_price=candle.close,
            fallback_used=fallback_used, provider_count_checked=1,
            resolution_confidence=1.0 if not fallback_used else (candle.quality_score or 0) / 100.0,
        ))
        row.unresolved_reason = None
        row.next_retry_at = None
        resolved += 1

    db.commit()
    return resolved
