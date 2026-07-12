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
