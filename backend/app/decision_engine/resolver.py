"""Prediction-resolution ledger worker.

Resolves due predictions from stored market candles only, after their
deadline has passed. When the primary source (Binance USDT-M futures - the
exchange every prediction's reference price was generated against) has a
verified gap for the exact due candle, this backfills from a small set of
public, read-only, multi-exchange fallbacks (app/data_sources/resolution_providers.py)
before giving up. It never forces early resolution, never fabricates a price,
and never overwrites an existing (already-idempotent, unique-constrained)
resolution row.
"""
import asyncio
from datetime import datetime, timezone
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.data_sources import resolution_providers as providers
from app.data_sources import symbol_map
from app.data_sources.downloader import store_candles
from app.data_sources.normalizer import TIMEFRAMES_MS
from app.db.models import MarketCandle, PredictionLedger, PredictionResolution
from app.decision_engine import outcome
from app.monitoring.logging import get_logger, log_event

logger = get_logger("quantx.resolver")

# Exhaustive set - every unresolved prediction must land in exactly one of these.
UNRESOLVED_STATUSES = (
    "awaiting_horizon", "due_for_resolution", "awaiting_source_candle",
    "primary_provider_unavailable", "primary_market_data_gap", "secondary_provider_pending",
    "resolver_delayed", "resolver_error", "unsupported_symbol", "unsupported_timeframe",
    "invalid_due_time", "missing_entry_price", "legacy_missing_metadata",
    "exchange_price_disagreement", "permanent_data_gap",
)

# Beyond Binance (the original source for every prediction today), try these
# in order. Spot is opt-in only (settings.resolver_allow_spot_fallback).
_FALLBACK_ORDER = ["coinbase", "bybit", "okx", "hyperliquid"]

_PERMANENT_GAP_ATTEMPTS = 5  # after this many failed attempts, a due prediction is a permanent gap, not a transient delay


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite round-trips DateTime columns as naive - this codebase's
    established convention (see [[quantx-horizon-deploy-80b872d]]) is that a
    naive value read back from the DB is always UTC, never local time."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def classify_unresolved_reason(row: PredictionLedger, now: datetime) -> str:
    """Pure function: what bucket does this still-unresolved row belong in
    right now. Never mutates, never resolves - callers persist separately."""
    deadline = _as_utc(row.resolution_deadline)
    generated = _as_utc(row.generated_at)
    if deadline is None or generated is None or deadline < generated:
        return "invalid_due_time"
    if row.reference_price is None:
        return "missing_entry_price"
    if row.direction not in ("LONG", "SHORT", "NEUTRAL", "NO_TRADE"):
        # NO_TRADE is a legitimate, common candidate direction (the source
        # explicitly chose not to call a direction this cycle) - it was
        # missing from this allow-list, so every NO_TRADE row that ever
        # failed one resolution attempt got mislabeled "legacy_missing_metadata"
        # (implying corrupt/missing metadata) instead of its real failure
        # reason, masking ~9k genuinely-recoverable rows behind a name that
        # sounds terminal. Only a truly absent/garbage direction value lands
        # here now.
        return "legacy_missing_metadata"
    if deadline > now:
        return "awaiting_horizon"
    if row.timeframe not in TIMEFRAMES_MS:
        return "unsupported_timeframe"
    attempts = row.resolver_attempts or 0
    if attempts == 0:
        return "due_for_resolution"
    if row.last_resolver_error == "unsupported_symbol":
        return "unsupported_symbol"
    if attempts >= _PERMANENT_GAP_ATTEMPTS:
        return "permanent_data_gap"
    if row.last_resolver_error == "exchange_price_disagreement":
        return "exchange_price_disagreement"
    if row.last_resolver_error == "primary_provider_unavailable":
        return "primary_provider_unavailable"
    if row.last_resolver_error == "primary_market_data_gap":
        # symbol_map-supported symbols still have fallback providers left to try next cycle
        return "secondary_provider_pending" if symbol_map.supported(row.symbol) else "primary_market_data_gap"
    if row.last_resolver_error == "resolver_error":
        return "resolver_error"
    return "resolver_delayed"


def _first_local_candle(db: Session, symbol: str, timeframe: str, at_ms: int, timeframe_ms: int):
    """Select the outcome candle for a deadline: the bucket containing the
    deadline (its close is the first close at/after it), or - bounded grace -
    the very next bucket when the deadline's own bucket is missing. Candle
    timestamps are bucket-aligned while deadlines are generated_at + horizon
    (arbitrary wall time), so an exact timestamp match can never succeed;
    the bounded window keeps the original policy of never letting an
    arbitrarily later candle resolve a prediction whose target-time market
    data is missing."""
    bucket_start = (at_ms // timeframe_ms) * timeframe_ms
    return (
        db.query(MarketCandle)
        .filter(MarketCandle.symbol == symbol, MarketCandle.timeframe == timeframe,
                MarketCandle.timestamp >= bucket_start,
                MarketCandle.timestamp <= at_ms + timeframe_ms)
        .order_by(MarketCandle.timestamp)
        .first()
    )


async def _backfill_from_providers(db: Session, row: PredictionLedger, at_ms: int, tf_ms: int) -> tuple[str | None, dict]:
    """Try Binance first (the primary/original source), then the fallback
    chain only for a verified gap. Returns (error_code_or_None, detail).

    Live provider calls are scoped to symbol_map.CANONICAL_SYMBOLS
    (BTCUSDT/ETHUSDT only, matching this feature's stated scope) - every
    other symbol (including every synthetic test symbol in this suite) never
    makes a network call and keeps the pre-existing local-candle-only
    behavior, unchanged."""
    detail: dict = {"providers_checked": []}
    if not symbol_map.supported(row.symbol):
        return "unsupported_symbol", detail

    primary = await providers.fetch_binance_futures(row.symbol, row.timeframe, at_ms, tf_ms)
    detail["providers_checked"].append(primary.provider)
    if primary.ok:
        store_candles(db, row.symbol, row.timeframe, "binance_futures",
                      [{"time": primary.actual_market_timestamp, "open": primary.price, "high": primary.price,
                        "low": primary.price, "close": primary.price, "volume": 0.0}], 100.0)
        detail.update(resolution_provider="binance_futures", resolution_exchange="binance",
                      resolution_market_type="usdt_perp", provider_symbol=row.symbol,
                      resolved_market_timestamp=primary.actual_market_timestamp, resolved_price=primary.price,
                      fallback_used=False, provider_count_checked=1, resolution_confidence=primary.confidence)
        return None, detail

    primary_error = "primary_provider_unavailable" if "unsupported" not in (primary.error or "") else "unsupported_timeframe"
    if primary.error == "no_data":
        primary_error = "primary_market_data_gap"
    if primary_error == "unsupported_timeframe":
        return "unsupported_timeframe", detail

    fallback_names = list(_FALLBACK_ORDER)
    if settings.resolver_allow_spot_fallback:
        fallback_names.append("binance_spot")

    observations: list[providers.ResolutionPriceObservation] = []
    for name in fallback_names:
        fetcher = providers.PROVIDER_FETCHERS[name]
        obs = await fetcher(row.symbol, row.timeframe, at_ms, tf_ms)
        detail["providers_checked"].append(name)
        if obs.ok:
            observations.append(obs)
        if len(observations) >= 2:
            break

    if not observations:
        return primary_error, detail  # every fallback also failed/unavailable - the primary reason still stands

    if len(observations) == 1:
        obs = observations[0]
        spread_bps = None
        confidence = obs.confidence * 0.7  # single independent source - discounted confidence
        fallback_reason = "single_provider_available"
    else:
        a, b = observations[0], observations[1]
        mid = (a.price + b.price) / 2
        spread_bps = abs(a.price - b.price) / mid * 10_000 if mid else None
        if spread_bps is not None and spread_bps > settings.resolver_price_disagreement_bps:
            detail["spread_bps"] = spread_bps
            detail["disagreeing_providers"] = [a.provider, b.provider]
            return "exchange_price_disagreement", detail
        obs = providers.ResolutionPriceObservation(
            provider=f"{a.provider}+{b.provider}", exchange=f"{a.exchange}+{b.exchange}",
            market_type=a.market_type, symbol=row.symbol, requested_timestamp=at_ms,
            actual_market_timestamp=a.actual_market_timestamp, price=mid, confidence=min(a.confidence, b.confidence),
        )
        confidence = obs.confidence
        fallback_reason = f"binance_gap_{primary_error}"

    canonical_provider = observations[0].provider
    store_candles(db, row.symbol, row.timeframe, canonical_provider,
                  [{"time": obs.actual_market_timestamp, "open": obs.price, "high": obs.price,
                    "low": obs.price, "close": obs.price, "volume": 0.0}], confidence * 100.0)
    detail.update(resolution_provider=obs.provider, resolution_exchange=obs.exchange,
                  resolution_market_type=observations[0].market_type, provider_symbol=row.symbol,
                  resolved_market_timestamp=obs.actual_market_timestamp, resolved_price=obs.price,
                  fallback_used=True, fallback_reason=fallback_reason, provider_count_checked=len(detail["providers_checked"]),
                  provider_price_spread_bps=spread_bps, resolution_confidence=confidence)
    return None, detail


def _mark_attempt(row: PredictionLedger, now: datetime, error: str | None) -> None:
    row.resolver_attempts = (row.resolver_attempts or 0) + 1
    row.last_resolver_attempt_at = now
    row.last_resolver_error = error
    row.unresolved_status = classify_unresolved_reason(row, now) if error else None
    row.lifecycle_status = outcome.lifecycle_status_for_attempt(row.unresolved_status) if error else row.lifecycle_status
    row.resolver_claim_token = None
    row.resolver_claimed_at = None
    if error:
        delay = min(3600, 60 * (2 ** min(max((row.resolver_attempts or 1) - 1, 0), 6)))
        row.resolver_next_attempt_at = now + timedelta(seconds=delay)
    else:
        row.resolver_next_attempt_at = None


def _claim_due_rows(db: Session, now: datetime, batch_size: int) -> tuple[str, list[PredictionLedger]]:
    """Claim a bounded mixed-priority batch. 75% serves newest/current due
    predictions promptly; 25% drains the oldest backlog gradually. PostgreSQL
    uses SKIP LOCKED. SQLite uses conditional token updates for deterministic
    tests and single-node development."""
    token = str(uuid4())
    stale_before = now - timedelta(seconds=settings.resolver_claim_timeout_seconds)
    base = (
        db.query(PredictionLedger)
        .outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(
            PredictionResolution.id.is_(None),
            PredictionLedger.resolution_deadline <= now,
            PredictionLedger.reference_price.isnot(None),
            or_(PredictionLedger.resolver_next_attempt_at.is_(None), PredictionLedger.resolver_next_attempt_at <= now),
            or_(PredictionLedger.resolver_claim_token.is_(None), PredictionLedger.resolver_claimed_at < stale_before),
            or_(PredictionLedger.unresolved_status.is_(None), PredictionLedger.unresolved_status != "permanent_data_gap"),
        )
    )
    recent_count = max(1, int(batch_size * 0.75))
    old_count = max(0, batch_size - recent_count)
    if db.bind.dialect.name == "postgresql":
        recent = base.order_by(PredictionLedger.resolution_deadline.desc()).with_for_update(skip_locked=True).limit(recent_count).all()
        recent_ids = {r.prediction_id for r in recent}
        old_q = base
        if recent_ids:
            old_q = old_q.filter(PredictionLedger.prediction_id.notin_(recent_ids))
        old = old_q.order_by(PredictionLedger.resolution_deadline.asc()).with_for_update(skip_locked=True).limit(old_count).all()
        rows = recent + old
        for row in rows:
            row.resolver_claim_token = token
            row.resolver_claimed_at = now
        db.commit()
    else:
        recent_ids = [r[0] for r in base.with_entities(PredictionLedger.prediction_id).order_by(
            PredictionLedger.resolution_deadline.desc()).limit(recent_count).all()]
        old_q = base.with_entities(PredictionLedger.prediction_id)
        if recent_ids:
            old_q = old_q.filter(PredictionLedger.prediction_id.notin_(recent_ids))
        ids = recent_ids + [r[0] for r in old_q.order_by(PredictionLedger.resolution_deadline.asc()).limit(old_count).all()]
        if ids:
            db.query(PredictionLedger).filter(
                PredictionLedger.prediction_id.in_(ids),
                or_(PredictionLedger.resolver_claim_token.is_(None), PredictionLedger.resolver_claimed_at < stale_before),
            ).update({PredictionLedger.resolver_claim_token: token, PredictionLedger.resolver_claimed_at: now}, synchronize_session=False)
            db.commit()
        rows = db.query(PredictionLedger).filter(PredictionLedger.resolver_claim_token == token).order_by(
            PredictionLedger.resolution_deadline.desc()).all()
    return token, rows


def _resolve_one(db: Session, row: PredictionLedger, candle: MarketCandle, now: datetime, provenance: dict) -> None:
    actual_return = (float(candle.close) - row.reference_price) / row.reference_price
    path = (
        db.query(MarketCandle)
        .filter(MarketCandle.symbol == row.symbol, MarketCandle.timeframe == row.timeframe,
                MarketCandle.timestamp >= int(row.generated_at.timestamp() * 1000),
                MarketCandle.timestamp <= int(row.resolution_deadline.timestamp() * 1000))
        .order_by(MarketCandle.timestamp).all()
    )
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
    # Symmetric neutral band (settings.resolution_neutral_band): a move whose
    # magnitude never clears the band is NEUTRAL for both LONG and SHORT
    # predictions, and neutral outcomes stay out of the directional-accuracy
    # denominator (correct stays NULL) instead of counting a sub-noise move
    # as a win or a loss.
    band = abs(settings.resolution_neutral_band)
    actual_direction = "NEUTRAL" if abs(actual_return) <= band else "LONG" if actual_return > 0 else "SHORT"
    correct = (None if (row.direction not in ("LONG", "SHORT") or actual_direction == "NEUTRAL")
               else row.direction == actual_direction)

    # Cost-aware outcome (app.decision_engine.outcome): computed and stored
    # alongside the fields above as additional, documented provenance for
    # the calibration dataset and dashboard - it does NOT replace `correct`/
    # `neutral_result` above, which remain the live resolver's authoritative
    # accuracy signal (repository.performance(), already-deployed confidence
    # calibration). Introducing cost-adjustment into that live signal would
    # be a strategy-threshold change, which this repair is explicitly not
    # authorized to make silently.
    cost_outcome = outcome.resolve_prediction_outcome(row.direction, row.reference_price, float(candle.close))
    if row.direction not in ("LONG", "SHORT"):
        lifecycle = outcome.RESOLVED_NEUTRAL
    elif correct is True:
        lifecycle = outcome.RESOLVED_CORRECT
    elif correct is False:
        lifecycle = outcome.RESOLVED_WRONG
    else:
        lifecycle = outcome.RESOLVED_NEUTRAL
    row.lifecycle_status = lifecycle

    db.add(PredictionResolution(
        prediction_id=row.prediction_id, actual_return=actual_return, resolved_direction=actual_direction,
        correct=correct, neutral_result=actual_direction == "NEUTRAL", target_hit=target_hit, stop_hit=stop_hit,
        maximum_favorable_excursion=mfe, maximum_adverse_excursion=mae,
        net_direction_adjusted_return=cost_outcome.net_direction_adjusted_return,
        estimated_fee=cost_outcome.estimated_fee, estimated_slippage=cost_outcome.estimated_slippage,
        neutral_band_used=cost_outcome.neutral_band_used, fee_rate_used=cost_outcome.fee_rate_used,
        slippage_bps_used=cost_outcome.slippage_bps_used,
        resolution_reason="fixed_horizon_close", resolved_at=now, requested_due_at=row.resolution_deadline,
        resolution_provider=provenance.get("resolution_provider"), resolution_exchange=provenance.get("resolution_exchange"),
        resolution_market_type=provenance.get("resolution_market_type"), provider_symbol=provenance.get("provider_symbol"),
        resolved_market_timestamp=provenance.get("resolved_market_timestamp"), resolved_price=provenance.get("resolved_price"),
        fallback_used=bool(provenance.get("fallback_used")), fallback_reason=provenance.get("fallback_reason"),
        provider_count_checked=provenance.get("provider_count_checked"),
        provider_price_spread_bps=provenance.get("provider_price_spread_bps"),
        resolution_confidence=provenance.get("resolution_confidence"),
    ))
    row.unresolved_status = None
    row.last_resolver_error = None


async def _process_claimed_rows(db: Session, rows: list[PredictionLedger], now: datetime, use_fallback: bool) -> dict:
    """Shared resolution loop for every claim queue (mixed/recent/historical).
    Never raises past its own bookkeeping - a single row's provider errors
    never abort the batch."""
    stats = {"scanned": len(rows), "resolved": 0, "primary_source": 0, "fallback_source": 0,
              "provider_disagreement": 0, "failed": 0, "by_reason": {}}

    for row in rows:
        at_ms = int(row.resolution_deadline.timestamp() * 1000)
        tf_ms = TIMEFRAMES_MS.get(row.timeframe)
        if tf_ms is None:
            _mark_attempt(row, now, "unsupported_timeframe")
            stats["failed"] += 1
            stats["by_reason"]["unsupported_timeframe"] = stats["by_reason"].get("unsupported_timeframe", 0) + 1
            continue

        candle = _first_local_candle(db, row.symbol, row.timeframe, at_ms, tf_ms)
        provenance: dict = {}
        if candle is not None:
            provenance = {"resolution_provider": candle.provider, "resolution_exchange": "binance" if "binance" in (candle.provider or "") else candle.provider,
                          "resolution_market_type": "usdt_perp", "provider_symbol": row.symbol,
                          "resolved_market_timestamp": candle.timestamp, "resolved_price": candle.close,
                          "fallback_used": candle.provider != "binance_futures", "provider_count_checked": 1,
                          "resolution_confidence": 1.0 if candle.provider == "binance_futures" else 0.8}
        elif use_fallback:
            try:
                error, detail = await _backfill_from_providers(db, row, at_ms, tf_ms)
            except Exception as exc:  # noqa: BLE001 - one row's failure must never abort the batch
                logger.warning("resolver backfill crashed for %s: %r", row.prediction_id, exc)
                error, detail = "resolver_error", {}
            if error:
                _mark_attempt(row, now, error)
                stats["failed"] += 1
                stats["by_reason"][error] = stats["by_reason"].get(error, 0) + 1
                if error == "exchange_price_disagreement":
                    stats["provider_disagreement"] += 1
                continue
            provenance = detail
            candle = _first_local_candle(db, row.symbol, row.timeframe, at_ms, tf_ms)
            if candle is None:
                _mark_attempt(row, now, "resolver_error")
                stats["failed"] += 1
                continue
        else:
            _mark_attempt(row, now, "primary_market_data_gap")
            stats["failed"] += 1
            stats["by_reason"]["primary_market_data_gap"] = stats["by_reason"].get("primary_market_data_gap", 0) + 1
            continue

        if not candle.close:
            _mark_attempt(row, now, "resolver_error")
            stats["failed"] += 1
            continue

        _resolve_one(db, row, candle, now, provenance)
        _mark_attempt(row, now, None)
        stats["resolved"] += 1
        if provenance.get("fallback_used"):
            stats["fallback_source"] += 1
        else:
            stats["primary_source"] += 1

    db.commit()
    return stats


def _release_stale_claims(db: Session, claim_token: str) -> None:
    """Defensive release if a row raised before normal attempt bookkeeping."""
    db.query(PredictionLedger).filter(PredictionLedger.resolver_claim_token == claim_token).update(
        {PredictionLedger.resolver_claim_token: None, PredictionLedger.resolver_claimed_at: None},
        synchronize_session=False,
    )
    db.commit()


async def resolve_due(db: Session, limit: int = 200, scan_limit: int | None = None, use_fallback: bool = True) -> dict:
    """Bounded catch-up cycle over the mixed (75% recent / 25% oldest) queue -
    unchanged from before the two-queue split, kept for existing callers/tests.
    Prefer resolve_recent_due/resolve_historical_backfill for new callers so
    the historical backlog can never delay current predictions."""
    now = datetime.now(timezone.utc)
    requested = min(limit, settings.resolver_batch_size)
    if scan_limit is not None:
        requested = min(requested, scan_limit)
    claim_token, rows = _claim_due_rows(db, now, requested)
    stats = await _process_claimed_rows(db, rows, now, use_fallback)
    _release_stale_claims(db, claim_token)
    log_event(logger, message="resolver_cycle_completed", category="prediction", **{k: v for k, v in stats.items() if k != "by_reason"})
    return stats


def _claim_rows_fair(db: Session, now: datetime, batch_size: int, *, newer_than: datetime | None,
                      older_than: datetime | None, oldest_fraction: float = 1.0,
                      retrying_priority_fraction: float = 0.5) -> tuple[str, list[PredictionLedger]]:
    """Starvation-free claim, scoped to one age band (recent-window or
    historical), replacing the pure newest-deadline-first claim that let a
    continuous stream of freshly-matured predictions permanently starve older
    PENDING/RETRYING rows once due volume exceeded one batch (2026-07-21
    fairness fix).

    Three-way reserved allocation, not two - an isolated-copy validation
    against the real production starvation snapshot showed that a simple
    oldest-vs-newest split still lets a large never-yet-attempted PENDING
    backlog starve RESOLUTION_ERROR_RETRYING rows within the "oldest" bucket,
    since PENDING rows are frequently chronologically even older than rows
    that already failed one attempt. So the oldest_fraction slice of the
    batch is itself split again:

      - retrying_priority_fraction of it goes to the oldest-eligible
        RESOLUTION_ERROR_RETRYING rows (respecting resolver_next_attempt_at),
      - the remainder goes to the oldest-eligible never-yet-attempted rows
        (PENDING, or legacy rows with no lifecycle_status yet).

    The rest of the batch (1 - oldest_fraction) goes to the newest-due rows
    of either status (fast handling of just-matured predictions). Unused
    capacity at any tier transfers to the others in the same cycle, so a
    quiet pool never wastes batch capacity. oldest_fraction=1.0
    (historical-backfill's default) still guarantees the PENDING/RETRYING
    split for its own scope, but skips the newest-due allocation entirely
    since that queue never prioritizes "newest" at all.

    Every row is claimed exactly once via an atomic conditional token update
    (SQLite) / SELECT FOR UPDATE SKIP LOCKED (PostgreSQL) - concurrent workers
    can never claim the same row. Every tier uses a stable
    (resolution_deadline, prediction_id) sort so ordering is deterministic
    across repeated calls with an identical row set."""
    token = str(uuid4())
    stale_before = now - timedelta(seconds=settings.resolver_claim_timeout_seconds)
    is_postgres = db.bind.dialect.name == "postgresql"
    RETRYING = outcome.RESOLUTION_ERROR_RETRYING

    def _base(exclude_ids: list[str] | None = None):
        q = (
            db.query(PredictionLedger)
            .outerjoin(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
            .filter(
                PredictionResolution.id.is_(None),
                PredictionLedger.resolution_deadline <= now,
                PredictionLedger.reference_price.isnot(None),
                or_(PredictionLedger.resolver_next_attempt_at.is_(None), PredictionLedger.resolver_next_attempt_at <= now),
                or_(PredictionLedger.resolver_claim_token.is_(None), PredictionLedger.resolver_claimed_at < stale_before),
                or_(PredictionLedger.unresolved_status.is_(None), PredictionLedger.unresolved_status != "permanent_data_gap"),
            )
        )
        if newer_than is not None:
            q = q.filter(PredictionLedger.generated_at >= newer_than)
        if older_than is not None:
            q = q.filter(PredictionLedger.generated_at < older_than)
        if exclude_ids:
            q = q.filter(~PredictionLedger.prediction_id.in_(exclude_ids))
        return q

    def _select_ids(q, order_desc: bool, limit: int) -> list[str]:
        if limit <= 0:
            return []
        order = (PredictionLedger.resolution_deadline.desc() if order_desc else PredictionLedger.resolution_deadline.asc(),
                 PredictionLedger.prediction_id.asc())
        if is_postgres:
            rows = q.order_by(*order).with_for_update(skip_locked=True).limit(limit).all()
            return [r.prediction_id for r in rows]
        return [r[0] for r in q.with_entities(PredictionLedger.prediction_id).order_by(*order).limit(limit).all()]

    oldest_fraction = max(0.0, min(1.0, oldest_fraction))
    retrying_priority_fraction = max(0.0, min(1.0, retrying_priority_fraction))
    oldest_count = round(batch_size * oldest_fraction)
    newest_count = batch_size - oldest_count
    retrying_count = round(oldest_count * retrying_priority_fraction)
    pending_count = oldest_count - retrying_count

    # Oldest-overdue RETRYING allocation - claimed first so a large PENDING
    # backlog can never crowd it out, regardless of relative pool size.
    retrying_ids = _select_ids(_base().filter(PredictionLedger.lifecycle_status == RETRYING),
                                order_desc=False, limit=retrying_count)

    # Oldest-overdue PENDING/never-attempted allocation, topped up with any
    # unused RETRYING capacity (few retries currently eligible).
    remaining_pending = pending_count + (retrying_count - len(retrying_ids))
    pending_ids = _select_ids(
        _base(exclude_ids=retrying_ids).filter(or_(PredictionLedger.lifecycle_status.is_(None),
                                                    PredictionLedger.lifecycle_status != RETRYING)),
        order_desc=False, limit=remaining_pending)

    # If PENDING also came up short, give the leftover back to RETRYING -
    # there may simply be more retry-eligible rows than pending ones right now.
    pending_shortfall = remaining_pending - len(pending_ids)
    if pending_shortfall > 0:
        extra_retrying_ids = _select_ids(
            _base(exclude_ids=retrying_ids + pending_ids).filter(PredictionLedger.lifecycle_status == RETRYING),
            order_desc=False, limit=pending_shortfall)
        retrying_ids = retrying_ids + extra_retrying_ids

    oldest_ids = retrying_ids + pending_ids

    # Newest-due allocation from whatever's left, topped up with any oldest
    # capacity that went unused across both oldest tiers.
    remaining_newest = newest_count + (oldest_count - len(oldest_ids))
    newest_ids = _select_ids(_base(exclude_ids=oldest_ids), order_desc=True, limit=remaining_newest)

    ids = oldest_ids + newest_ids
    if is_postgres:
        # _select_ids already issued SELECT ... FOR UPDATE SKIP LOCKED above,
        # locking these rows for this transaction - safe to stamp the token now.
        if ids:
            db.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).update(
                {PredictionLedger.resolver_claim_token: token, PredictionLedger.resolver_claimed_at: now},
                synchronize_session=False)
            db.commit()
    else:
        if ids:
            db.query(PredictionLedger).filter(
                PredictionLedger.prediction_id.in_(ids),
                or_(PredictionLedger.resolver_claim_token.is_(None), PredictionLedger.resolver_claimed_at < stale_before),
            ).update({PredictionLedger.resolver_claim_token: token, PredictionLedger.resolver_claimed_at: now}, synchronize_session=False)
            db.commit()
    rows = db.query(PredictionLedger).filter(PredictionLedger.resolver_claim_token == token).order_by(
        PredictionLedger.resolution_deadline.asc(), PredictionLedger.prediction_id.asc()).all()
    return token, rows


async def resolve_recent_due(db: Session, limit: int = 200, recent_window_hours: float = 6.0, use_fallback: bool = True) -> dict:
    """Recent-priority queue: only predictions generated within the last
    recent_window_hours. Resolves newly-matured predictions promptly no
    matter how large the historical backlog is."""
    now = datetime.now(timezone.utc)
    claim_token, rows = _claim_rows_fair(db, now, min(limit, settings.resolver_recent_batch_size),
                                        newer_than=now - timedelta(hours=recent_window_hours), older_than=None,
                                        oldest_fraction=settings.resolver_recent_oldest_allocation_fraction,
                                        retrying_priority_fraction=settings.resolver_recent_retrying_priority_fraction)
    stats = await _process_claimed_rows(db, rows, now, use_fallback)
    _release_stale_claims(db, claim_token)
    log_event(logger, message="resolver_recent_cycle_completed", category="prediction", **{k: v for k, v in stats.items() if k != "by_reason"})
    return stats


async def resolve_historical_backfill(db: Session, limit: int = 1000, recent_window_hours: float = 6.0, use_fallback: bool = True) -> dict:
    """Historical-backfill queue: only predictions older than
    recent_window_hours, processed in large controlled batches. Runs on its
    own schedule/loop, entirely independent of the recent queue's claims."""
    now = datetime.now(timezone.utc)
    # oldest_fraction=1.0: historical backfill has no "newest" component at
    # all - it always drains the true tail of its own scope first, which was
    # never subject to the starvation defect (see _claim_rows_fair docstring).
    claim_token, rows = _claim_rows_fair(db, now, min(limit, settings.resolver_backfill_batch_size),
                                        newer_than=None, older_than=now - timedelta(hours=recent_window_hours),
                                        oldest_fraction=1.0)
    stats = await _process_claimed_rows(db, rows, now, use_fallback)
    _release_stale_claims(db, claim_token)
    log_event(logger, message="resolver_backfill_cycle_completed", category="prediction", **{k: v for k, v in stats.items() if k != "by_reason"})
    return stats


def resolve_due_sync(db: Session, limit: int = 200, scan_limit: int | None = None, use_fallback: bool = True) -> dict:
    """Sync entrypoint for callers not already inside a running event loop
    (scripts, tests, admin one-off triggers). Inside a running loop (e.g. a
    FastAPI request handler) `await resolve_due(...)` directly instead."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(resolve_due(db, limit, scan_limit, use_fallback))
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(resolve_due(db, limit, scan_limit, use_fallback))).result()
