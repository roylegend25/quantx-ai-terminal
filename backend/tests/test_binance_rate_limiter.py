"""app/exchanges/binance_rate_limiter.py - the process-wide Binance REST
governor every BinanceFuturesClient call funnels through (Phase 29)."""

import time

import pytest

from app.exchanges.binance_rate_limiter import (
    CRITICAL,
    HIGH,
    LOW,
    NORMAL,
    BinanceCooldownActive,
    BinanceRateLimiter,
)


@pytest.fixture
def rl():
    return BinanceRateLimiter(backoff_multiplier=2.0)


def test_normal_calls_pass_through_with_no_prior_throttling(rl):
    rl.before_request("/fapi/v1/openOrders", HIGH)  # must not raise
    status = rl.status()
    assert status["rate_limited"] is False
    assert status["requests_per_minute"] == 1


def test_429_sets_a_cooldown_that_blocks_low_and_normal_priority(rl):
    rl.after_response("/fapi/v2/account", 429, {"Retry-After": "5"})
    status = rl.status()
    assert status["rate_limited"] is True
    assert status["retry_after_seconds"] == pytest.approx(5.0, abs=0.5)

    with pytest.raises(BinanceCooldownActive):
        rl.before_request("/fapi/v1/income", LOW)
    with pytest.raises(BinanceCooldownActive):
        rl.before_request("/fapi/v2/balance", NORMAL)

    # HIGH and CRITICAL still get through during an ordinary 429 cooldown -
    # withholding live positions/close/cancel is more dangerous than one
    # more request.
    rl.before_request("/fapi/v2/positionRisk", HIGH)
    rl.before_request("/fapi/v1/order", CRITICAL)


def test_418_bans_everything_except_critical(rl):
    rl.after_response("/fapi/v1/order", 418, {"Retry-After": "30"})
    status = rl.status()
    assert status["banned"] is True

    for priority in (LOW, NORMAL, HIGH):
        with pytest.raises(BinanceCooldownActive) as exc:
            rl.before_request("/fapi/v2/positionRisk", priority)
        assert exc.value.banned is True

    rl.before_request("/fapi/v1/order", CRITICAL)  # emergency close/cancel must still get a chance


def test_consecutive_429s_grow_the_cooldown_by_the_backoff_multiplier(rl):
    rl.after_response("/x", 429, {})  # no Retry-After -> default base
    first_cooldown = rl._cooldown_until
    first_backoff = first_cooldown - time.time()

    rl.after_response("/x", 429, {})
    second_backoff = rl._cooldown_until - time.time()

    # second backoff should be roughly backoff_multiplier times the first
    # (both measured from "now", so allow generous tolerance for test wall-clock drift)
    assert second_backoff > first_backoff


def test_a_clean_response_resets_the_consecutive_429_streak(rl):
    rl.after_response("/x", 429, {})
    rl.after_response("/x", 200, {})
    assert rl._consecutive_429 == 0


def test_weight_and_order_count_headers_are_recorded(rl):
    rl.after_response("/fapi/v2/account", 200, {"X-MBX-USED-WEIGHT-1M": "42", "X-MBX-ORDER-COUNT-1M": "3"})
    status = rl.status()
    assert status["weight_used_1m"] == 42
    assert status["order_count_used_1m"] == 3


def test_status_reports_top_endpoints_by_call_count(rl):
    for _ in range(3):
        rl.before_request("/fapi/v2/positionRisk", HIGH)
    rl.before_request("/fapi/v1/openOrders", HIGH)
    top = rl.status()["top_endpoints"]
    assert top[0] == {"endpoint": "/fapi/v2/positionRisk", "count": 3}


def test_reset_clears_all_state(rl):
    rl.after_response("/x", 429, {})
    rl.reset()
    status = rl.status()
    assert status["rate_limited"] is False
    assert status["requests_per_minute"] == 0


# ================================================ Debug 2.pdf: cooldown cap

def test_consecutive_429s_never_compound_past_the_cap(rl):
    """Root cause of a cooldown that 'never clears' (observed live: 'retry
    in 985s'): HIGH/CRITICAL calls are exempt from the soft-cooldown gate,
    so if Binance is still genuinely limiting the IP, each one can land
    another 429 and re-extend an uncapped exponential backoff before the
    previous cooldown ever expires. 14+ consecutive violations with the
    default multiplier should be capped, not left to compound to 900+s."""
    for _ in range(20):
        rl.after_response("/fapi/v2/positionRisk", 429, {"Retry-After": "5"})
    backoff = rl._cooldown_until - time.time()
    assert backoff <= 120.5  # MAX_SOFT_COOLDOWN_SECONDS + slack for wall-clock drift


def test_explicit_long_retry_after_from_binance_still_respected(rl):
    """The cap bounds OUR OWN multiplier growth, not a single explicit,
    valid Retry-After Binance itself sends on a given response."""
    rl.after_response("/fapi/v2/positionRisk", 429, {"Retry-After": "300"})
    backoff = rl._cooldown_until - time.time()
    assert backoff == pytest.approx(300.0, abs=1.0)


def test_a_successful_response_clears_an_active_cooldown_immediately(rl):
    """Section 3: 'when cooldown expires... if successful, clear cooldown,
    reset repeated-429 counter, mark healthy' - a live 2xx is stronger
    evidence than waiting out an old timer, especially one that was
    computed from a since-resolved burst of violations."""
    rl.after_response("/fapi/v2/positionRisk", 429, {"Retry-After": "60"})
    assert rl.status()["rate_limited"] is True

    rl.after_response("/fapi/v2/positionRisk", 200, {})

    status = rl.status()
    assert status["rate_limited"] is False
    assert status["cooldown_active"] is False
    assert status["consecutive_429s"] == 0
    assert status["last_success_at"] is not None


def test_a_success_during_a_ban_does_not_clear_the_ban(rl):
    """A hard 418 ban is only ever survivable by CRITICAL calls, and a
    single such success is deliberately not trusted to end an IP-level ban
    early - unlike a soft cooldown, it isn't purely this process's own
    guess at Binance's mood."""
    rl.after_response("/fapi/v1/order", 418, {"Retry-After": "30"})
    rl.after_response("/fapi/v1/order", 200, {})  # e.g. a CRITICAL call got through
    assert rl.status()["ban_active"] is True


# ------------------------------------------------------ Retry-After parsing

def test_retry_after_plain_seconds_parsed_as_seconds(rl):
    rl.after_response("/x", 429, {"Retry-After": "42"})
    backoff = rl._cooldown_until - time.time()
    assert backoff == pytest.approx(42.0, abs=1.0)


def test_retry_after_millisecond_epoch_timestamp_converted_to_seconds(rl):
    future_ms = str(int((time.time() + 90) * 1000))
    rl.after_response("/x", 429, {"Retry-After": future_ms})
    backoff = rl._cooldown_until - time.time()
    assert backoff == pytest.approx(90.0, abs=2.0)


def test_retry_after_bare_epoch_seconds_timestamp_converted_to_seconds(rl):
    future_s = str(int(time.time() + 45))
    rl.after_response("/x", 429, {"Retry-After": future_s})
    backoff = rl._cooldown_until - time.time()
    assert backoff == pytest.approx(45.0, abs=2.0)


def test_retry_after_absent_uses_capped_exponential_backoff(rl):
    for _ in range(25):
        rl.after_response("/x", 429, {})
    backoff = rl._cooldown_until - time.time()
    assert 0 < backoff <= 120.5


def test_status_exposes_new_debug_fields(rl):
    status = rl.status()
    assert status["cooldown_active"] is False
    assert status["ban_active"] is False
    assert status["consecutive_429s"] == 0
    assert status["last_success_at"] is None
    assert status["source"] == "rate_limiter"
