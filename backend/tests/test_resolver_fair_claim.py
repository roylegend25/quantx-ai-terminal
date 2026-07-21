"""Starvation-free claim allocation (2026-07-21 recent-queue fairness fix).

_claim_rows_fair replaces the pure newest-deadline-first claim that let a
continuous stream of freshly-matured predictions permanently starve older
PENDING/RETRYING rows once due volume exceeded one batch. These tests operate
directly on _claim_rows_fair (pure DB claim/selection logic - no network,
no MarketCandle, no provider calls needed) so they can cheaply simulate
hundreds of eligible rows and multiple cycles.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine import outcome
from app.decision_engine.resolver import _PERMANENT_GAP_ATTEMPTS, _claim_rows_fair, _release_stale_claims, resolve_recent_due

PREFIX = "fair-claim-test-"


def _ledger(pid, deadline_offset_minutes, symbol="BTCUSDT", direction="LONG",
            lifecycle_status="PENDING", resolver_attempts=0, resolver_next_attempt_at=None,
            generated_offset_minutes=None):
    now = datetime.now(timezone.utc)
    deadline = now - timedelta(minutes=deadline_offset_minutes)
    generated = now - timedelta(minutes=generated_offset_minutes if generated_offset_minutes is not None else deadline_offset_minutes + 5)
    return PredictionLedger(
        prediction_id=PREFIX + pid, candidate_id=f"cand-{PREFIX}{pid}", decision_id="d", user_id="admin",
        engine="active_drive_v2", engine_version="2.2.0", source_type="strategy", source_name="trend", source_version="1",
        symbol=symbol, timeframe="5m", direction=direction, confidence=0.7,
        target_horizon_seconds=300, feature_snapshot_hash=f"h-{pid}", generated_at=generated,
        resolution_deadline=deadline, reference_price=100.0, lifecycle_status=lifecycle_status,
        resolver_attempts=resolver_attempts, resolver_next_attempt_at=resolver_next_attempt_at,
    )


@pytest.fixture
def db():
    session = SessionLocal()
    ids = [r[0] for r in session.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like(f"{PREFIX}%")).all()]
    if ids:
        session.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    yield session
    ids = [r[0] for r in session.query(PredictionLedger.prediction_id).filter(PredictionLedger.prediction_id.like(f"{PREFIX}%")).all()]
    if ids:
        session.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.query(PredictionLedger).filter(PredictionLedger.prediction_id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    session.close()


def _release(db, token):
    """Free a claim without going through the full resolve pipeline, so the
    next simulated cycle can re-claim eligible rows exactly like a real
    worker would after finishing (success or failure) with its batch."""
    _release_stale_claims(db, token)


def test_1300_rows_batch_100_oldest_advances_every_cycle(db):
    """The core regression: with far more eligible rows than one batch can
    hold, the oldest-due row must still be claimed - not starved forever by
    a continuous stream of newer rows."""
    now = datetime.now(timezone.utc)
    # Spread deadlines across ~250 minutes (well within the 6h recent-window
    # scope, including the +5min generated_at margin _ledger adds) so every
    # row is actually eligible for the recent queue, not silently routed out
    # to the historical queue's scope by generated_at falling outside 6h.
    for i in range(1300):
        db.add(_ledger(f"row-{i:05d}", deadline_offset_minutes=(1300 - i) * 250 / 1300))
    db.commit()

    token, rows = _claim_rows_fair(db, now, 100, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=0.5)
    claimed_ids = {r.prediction_id for r in rows}
    assert len(rows) == 100
    # The single oldest row in the whole 1300-row population must be in this very first batch.
    assert PREFIX + "row-00000" in claimed_ids
    # Roughly half-and-half per the 0.5 fraction.
    oldest_claimed = sum(1 for r in rows if r.prediction_id.endswith(tuple(f"{i:05d}" for i in range(0, 650))))
    assert oldest_claimed >= 45  # allow slack for tie-break rounding, but must be a real share, not zero


def test_continuous_new_arrivals_do_not_prevent_oldest_row_progress(db):
    """Simulates the exact failure mode reported: every cycle, new rows keep
    arriving with newer deadlines than everything already due. The oldest
    row must still be claimed within a bounded number of cycles, not never."""
    now = datetime.now(timezone.utc)
    # 500 old rows, already all overdue, spread across ~200 minutes - well
    # within the 6h recent-window scope (including _ledger's +5min generated_at
    # margin), so every one of them is actually eligible for the recent queue.
    for i in range(500):
        db.add(_ledger(f"old-{i:04d}", deadline_offset_minutes=(500 - i) * 200 / 500))
    db.commit()

    oldest_id = PREFIX + "old-0000"
    claimed_ever = set()
    for cycle in range(10):
        cycle_now = datetime.now(timezone.utc)
        # A fresh wave of 80 brand-new due rows arrives every cycle - always
        # newer than anything claimed so far, exactly like live production.
        for i in range(80):
            db.add(_ledger(f"new-{cycle:03d}-{i:03d}", deadline_offset_minutes=0, generated_offset_minutes=1))
        db.commit()
        token, rows = _claim_rows_fair(db, cycle_now, 100, newer_than=cycle_now - timedelta(hours=6),
                                       older_than=None, oldest_fraction=0.5)
        claimed_ever.update(r.prediction_id for r in rows)
        _release(db, token)
        if oldest_id in claimed_ever:
            break
    assert oldest_id in claimed_ever, "oldest row was starved across 10 cycles despite continuous new arrivals"


def test_pure_newest_first_would_have_starved_the_same_scenario(db):
    """Sanity check that the test above is actually exercising the bug this
    fix targets - with oldest_fraction=0 (pure newest-first, the old
    behavior), the oldest row is never reached."""
    now = datetime.now(timezone.utc)
    for i in range(500):
        db.add(_ledger(f"old-{i:04d}", deadline_offset_minutes=(500 - i) * 200 / 500))
    db.commit()
    oldest_id = PREFIX + "old-0000"
    claimed_ever = set()
    for cycle in range(10):
        cycle_now = datetime.now(timezone.utc)
        for i in range(80):
            db.add(_ledger(f"new2-{cycle:03d}-{i:03d}", deadline_offset_minutes=0, generated_offset_minutes=1))
        db.commit()
        token, rows = _claim_rows_fair(db, cycle_now, 100, newer_than=cycle_now - timedelta(hours=6),
                                       older_than=None, oldest_fraction=0.0)
        claimed_ever.update(r.prediction_id for r in rows)
        _release(db, token)
    assert oldest_id not in claimed_ever


def test_oldest_pending_row_eventually_claimed(db):
    now = datetime.now(timezone.utc)
    db.add(_ledger("pending-old", deadline_offset_minutes=100, lifecycle_status="PENDING"))
    for i in range(200):
        db.add(_ledger(f"pending-new-{i:03d}", deadline_offset_minutes=1))
    db.commit()
    token, rows = _claim_rows_fair(db, now, 100, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=0.5)
    assert PREFIX + "pending-old" in {r.prediction_id for r in rows}


def test_oldest_retrying_row_eventually_claimed(db):
    now = datetime.now(timezone.utc)
    db.add(_ledger("retrying-old", deadline_offset_minutes=100, lifecycle_status="RESOLUTION_ERROR_RETRYING",
                   resolver_attempts=1, resolver_next_attempt_at=now - timedelta(seconds=5)))
    for i in range(200):
        db.add(_ledger(f"retrying-new-{i:03d}", deadline_offset_minutes=1))
    db.commit()
    token, rows = _claim_rows_fair(db, now, 100, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=0.5)
    assert PREFIX + "retrying-old" in {r.prediction_id for r in rows}


def test_pending_and_retrying_both_make_progress_in_one_batch(db):
    now = datetime.now(timezone.utc)
    db.add(_ledger("mixed-pending", deadline_offset_minutes=50, lifecycle_status="PENDING"))
    db.add(_ledger("mixed-retrying", deadline_offset_minutes=49, lifecycle_status="RESOLUTION_ERROR_RETRYING",
                   resolver_attempts=2, resolver_next_attempt_at=now - timedelta(seconds=5)))
    db.commit()
    token, rows = _claim_rows_fair(db, now, 10, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=0.5)
    ids = {r.prediction_id for r in rows}
    assert PREFIX + "mixed-pending" in ids
    assert PREFIX + "mixed-retrying" in ids


def test_large_pending_backlog_does_not_starve_retrying_rows(db):
    """The exact real-production failure mode found during isolated
    validation: a huge never-yet-attempted PENDING backlog, all with older
    deadlines than a small pool of RESOLUTION_ERROR_RETRYING rows, must not
    prevent the RETRYING rows from being claimed - PENDING and RETRYING are
    reserved separate capacity within the oldest allocation, not just pooled
    together by deadline alone."""
    now = datetime.now(timezone.utc)
    # 400 PENDING rows, all with older deadlines than the retrying rows below.
    for i in range(400):
        db.add(_ledger(f"big-pending-{i:03d}", deadline_offset_minutes=100 + i, lifecycle_status="PENDING"))
    # 10 RETRYING rows, deadlines newer than every PENDING row above, but
    # already eligible for another attempt.
    for i in range(10):
        db.add(_ledger(f"small-retrying-{i:03d}", deadline_offset_minutes=50 + i,
                       lifecycle_status="RESOLUTION_ERROR_RETRYING", resolver_attempts=1,
                       resolver_next_attempt_at=now - timedelta(seconds=5)))
    db.commit()
    token, rows = _claim_rows_fair(db, now, 100, newer_than=now - timedelta(hours=6), older_than=None,
                                   oldest_fraction=0.5, retrying_priority_fraction=0.5)
    ids = {r.prediction_id for r in rows}
    retrying_claimed = sum(1 for i in range(10) if PREFIX + f"small-retrying-{i:03d}" in ids)
    assert retrying_claimed == 10, f"expected all 10 retrying rows claimed despite the larger pending backlog, got {retrying_claimed}"


def test_large_retrying_backlog_does_not_starve_pending_rows(db):
    """Symmetric case: a huge RETRYING backlog must not starve a small
    PENDING pool either."""
    now = datetime.now(timezone.utc)
    for i in range(400):
        db.add(_ledger(f"big-retrying-{i:03d}", deadline_offset_minutes=100 + i,
                       lifecycle_status="RESOLUTION_ERROR_RETRYING", resolver_attempts=1,
                       resolver_next_attempt_at=now - timedelta(seconds=5)))
    for i in range(10):
        db.add(_ledger(f"small-pending-{i:03d}", deadline_offset_minutes=50 + i, lifecycle_status="PENDING"))
    db.commit()
    token, rows = _claim_rows_fair(db, now, 100, newer_than=now - timedelta(hours=6), older_than=None,
                                   oldest_fraction=0.5, retrying_priority_fraction=0.5)
    ids = {r.prediction_id for r in rows}
    pending_claimed = sum(1 for i in range(10) if PREFIX + f"small-pending-{i:03d}" in ids)
    assert pending_claimed == 10, f"expected all 10 pending rows claimed despite the larger retrying backlog, got {pending_claimed}"


def test_next_attempt_at_in_the_future_excludes_row(db):
    now = datetime.now(timezone.utc)
    db.add(_ledger("not-yet-due-retry", deadline_offset_minutes=50, lifecycle_status="RESOLUTION_ERROR_RETRYING",
                   resolver_attempts=1, resolver_next_attempt_at=now + timedelta(minutes=10)))
    db.commit()
    token, rows = _claim_rows_fair(db, now, 10, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=0.5)
    assert PREFIX + "not-yet-due-retry" not in {r.prediction_id for r in rows}


def test_fair_capacity_transfers_to_newest_when_no_old_rows_exist(db):
    """oldest_fraction reserves capacity, but never wastes it - if there are
    no old-eligible rows at all, the newest allocation gets the full batch."""
    now = datetime.now(timezone.utc)
    for i in range(30):
        db.add(_ledger(f"onlynew-{i:03d}", deadline_offset_minutes=1))
    db.commit()
    token, rows = _claim_rows_fair(db, now, 100, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=0.5)
    assert len(rows) == 30  # all of them, capacity transfer didn't drop any


def test_fair_capacity_transfers_to_oldest_when_few_new_rows_exist(db):
    now = datetime.now(timezone.utc)
    for i in range(30):
        db.add(_ledger(f"onlyold-{i:03d}", deadline_offset_minutes=100 + i))
    db.commit()
    token, rows = _claim_rows_fair(db, now, 100, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=0.5)
    assert len(rows) == 30


def test_historical_backfill_oldest_fraction_1_is_unchanged_pure_oldest_first(db):
    """oldest_fraction=1.0 (historical-backfill's setting) must reproduce the
    original pure-oldest-first behavior exactly - no starvation risk existed
    there, so this fix must not alter its ordering."""
    now = datetime.now(timezone.utc)
    # hist-000 has the largest offset (20min ago) = oldest deadline; hist-019
    # has the smallest (1min ago) = newest. Oldest-first must return
    # hist-000..hist-004 in that order.
    for i in range(20):
        db.add(_ledger(f"hist-{i:03d}", deadline_offset_minutes=20 - i, generated_offset_minutes=500))
    db.commit()
    token, rows = _claim_rows_fair(db, now, 5, newer_than=None, older_than=now - timedelta(hours=6), oldest_fraction=1.0)
    claimed = [r.prediction_id for r in rows]
    expected_oldest_five = [PREFIX + f"hist-{i:03d}" for i in range(0, 5)]
    assert claimed == expected_oldest_five


def test_deterministic_ordering_stable_across_repeated_calls(db):
    """Two rows with an identical resolution_deadline must break ties on
    prediction_id consistently, not depend on insertion/scan order."""
    now = datetime.now(timezone.utc)
    same_deadline = now - timedelta(minutes=10)
    for pid in ("zzz", "aaa", "mmm"):
        row = _ledger(pid, deadline_offset_minutes=10)
        row.resolution_deadline = same_deadline
        db.add(row)
    db.commit()
    token1, rows1 = _claim_rows_fair(db, now, 1, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=1.0)
    assert rows1[0].prediction_id == PREFIX + "aaa"  # alphabetically first prediction_id wins the tie
    _release(db, token1)
    token2, rows2 = _claim_rows_fair(db, now, 1, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=1.0)
    assert rows2[0].prediction_id == PREFIX + "aaa"


def test_duplicate_claim_prevention_within_stale_window(db):
    """A row already claimed (fresh, not stale) by another in-flight cycle
    must never be claimed again until its claim goes stale or is released."""
    now = datetime.now(timezone.utc)
    db.add(_ledger("already-claimed", deadline_offset_minutes=10))
    db.commit()
    token1, rows1 = _claim_rows_fair(db, now, 10, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=1.0)
    assert len(rows1) == 1
    # A second, concurrent claim attempt at the same instant must not pick up the same row.
    token2, rows2 = _claim_rows_fair(db, now, 10, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=1.0)
    assert len(rows2) == 0


def test_stale_claim_is_recoverable_after_timeout(db):
    """Simulates a worker crash after claiming but before finishing (e.g. an
    unhandled exception, or the process dying) - the row must become
    reclaimable once resolver_claim_timeout_seconds has elapsed, not stuck
    forever holding a token nobody will ever release."""
    now = datetime.now(timezone.utc)
    row = _ledger("crashed-worker-row", deadline_offset_minutes=10)
    db.add(row)
    db.commit()
    token1, rows1 = _claim_rows_fair(db, now, 10, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=1.0)
    assert len(rows1) == 1
    # Simulate time passing well beyond resolver_claim_timeout_seconds without
    # ever releasing token1 (the crash - no _release_stale_claims call).
    later = now + timedelta(seconds=600)
    token2, rows2 = _claim_rows_fair(db, later, 10, newer_than=later - timedelta(hours=6), older_than=None, oldest_fraction=1.0)
    assert len(rows2) == 1
    assert rows2[0].prediction_id == PREFIX + "crashed-worker-row"


def test_no_lifecycle_status_is_rewritten_by_claiming_alone(db):
    """Claiming a row must never itself change lifecycle_status, direction,
    reference_price, or any outcome field - only stamps the claim token."""
    now = datetime.now(timezone.utc)
    db.add(_ledger("claim-only", deadline_offset_minutes=10, lifecycle_status="PENDING"))
    db.commit()
    _claim_rows_fair(db, now, 10, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=1.0)
    row = db.get(PredictionLedger, PREFIX + "claim-only")
    assert row.lifecycle_status == "PENDING"
    assert row.reference_price == 100.0
    assert row.direction == "LONG"


def test_retry_progresses_through_attempts_and_terminalizes_at_max(db):
    """End-to-end via the real recent-priority queue (resolve_recent_due),
    no network: a row on a symbol with no stored candles and use_fallback
    disabled fails every attempt deterministically. Proves attempt_count
    increments exactly once per cycle, next_attempt_at backoff is respected
    (each cycle uses a `now` past the prior backoff window), and the row
    terminalizes to VOID_DATA_GAP exactly at _PERMANENT_GAP_ATTEMPTS - never
    early, never stuck retrying forever."""
    now = datetime.now(timezone.utc)
    row = _ledger("progression-row", deadline_offset_minutes=10, symbol="NOFAIRCLAIMCANDLESUSDT")
    db.add(row)
    db.commit()

    seen_attempts = []
    for _ in range(_PERMANENT_GAP_ATTEMPTS + 2):
        asyncio.run(resolve_recent_due(db, limit=10, recent_window_hours=6.0, use_fallback=False))
        db.refresh(row)
        seen_attempts.append(row.resolver_attempts)
        if row.lifecycle_status in outcome.TERMINAL_STATUSES:
            break
        # Simulate time passing past this attempt's exponential backoff
        # window, exactly as would happen between real resolver ticks -
        # resolve_recent_due always uses the real wall clock internally, so
        # the test advances the row's own next_attempt_at instead of the
        # clock to make it eligible for the next cycle without sleeping.
        row.resolver_next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    assert seen_attempts == list(range(1, _PERMANENT_GAP_ATTEMPTS + 1)), \
        f"expected attempts to progress 1..{_PERMANENT_GAP_ATTEMPTS} exactly once each, got {seen_attempts}"
    assert row.lifecycle_status == outcome.VOID_DATA_GAP
    assert row.resolver_attempts == _PERMANENT_GAP_ATTEMPTS


def test_attempt_count_increments_exactly_once_per_claim(db):
    now = datetime.now(timezone.utc)
    row = _ledger("single-increment-row", deadline_offset_minutes=10, symbol="NOFAIRCLAIMCANDLESUSDT")
    db.add(row)
    db.commit()
    asyncio.run(resolve_recent_due(db, limit=10, recent_window_hours=6.0, use_fallback=False))
    db.refresh(row)
    assert row.resolver_attempts == 1
    # Immediately re-running the same cycle at the same instant must not
    # re-claim (still within backoff) and must not double-increment.
    asyncio.run(resolve_recent_due(db, limit=10, recent_window_hours=6.0, use_fallback=False))
    db.refresh(row)
    assert row.resolver_attempts == 1


def test_recent_resolution_latency_unaffected_when_old_pool_is_small(db):
    """With few old-eligible rows, almost the entire batch still goes to the
    newest ones - the fairness fix must not meaningfully slow down fresh
    predictions under normal (non-backlogged) conditions."""
    now = datetime.now(timezone.utc)
    db.add(_ledger("one-old-row", deadline_offset_minutes=200))
    for i in range(99):
        db.add(_ledger(f"fresh-{i:03d}", deadline_offset_minutes=1))
    db.commit()
    token, rows = _claim_rows_fair(db, now, 100, newer_than=now - timedelta(hours=6), older_than=None, oldest_fraction=0.5)
    assert len(rows) == 100  # all 100 due rows claimed in a single cycle - no fresh row left behind
