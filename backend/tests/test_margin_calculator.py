"""Pure math for the Live Margin Calculator (app/trading/margin_calculator.py).
No network, no DB - every formula is exercised in isolation so the
Execution Pipeline / Live Margin Calculator cards can trust the numbers."""

import pytest

from app.trading import margin_calculator as mc


# ------------------------------------------------------------------ rounding

def test_round_qty_floors_to_step_size():
    assert mc.round_qty(0.0129, 0.001, 3) == 0.012


def test_round_qty_zero_or_negative_is_zero():
    assert mc.round_qty(0.0, 0.001, 3) == 0.0
    assert mc.round_qty(-1.0, 0.001, 3) == 0.0


def test_round_price_rounds_to_tick_size():
    assert mc.round_price(63871.37, 0.1, 1) == 63871.4


# --------------------------------------------------------- maintenance margin

def test_maintenance_margin_uses_matching_bracket():
    brackets = [
        {"notional_cap": 50000.0, "notional_floor": 0.0, "maint_margin_ratio": 0.004, "cum": 0.0},
        {"notional_cap": 250000.0, "notional_floor": 50000.0, "maint_margin_ratio": 0.005, "cum": 50.0},
    ]
    ratio, cum, available = mc.maintenance_margin_for_notional(brackets, 80.0)
    assert (ratio, cum, available) == (0.004, 0.0, True)

    ratio2, cum2, _ = mc.maintenance_margin_for_notional(brackets, 100000.0)
    assert (ratio2, cum2) == (0.005, 50.0)


def test_maintenance_margin_falls_back_when_brackets_unavailable():
    ratio, cum, available = mc.maintenance_margin_for_notional([], 80.0)
    assert ratio == 0.004
    assert cum == 0.0
    assert available is False


# ------------------------------------------------------------- margin math

def test_margin_breakdown_matches_documented_formula():
    breakdown = mc.compute_margin_breakdown(
        notional=80.0, leverage=10.0, available_margin=10.0,
        taker_fee_rate=0.00075, maint_margin_ratio=0.004, maint_cum=0.0,
        safety_buffer_usdt=1.0, safety_buffer_pct=0.0,
    )
    assert breakdown.initial_margin == pytest.approx(8.0)
    assert breakdown.estimated_fees == pytest.approx(80.0 * 0.00075 * 2)
    assert breakdown.maintenance_margin == pytest.approx(80.0 * 0.004)
    assert breakdown.safety_buffer == pytest.approx(1.0)
    assert breakdown.total_required == pytest.approx(
        breakdown.initial_margin + breakdown.estimated_fees + breakdown.maintenance_margin + breakdown.safety_buffer
    )


def test_margin_breakdown_pass_when_available_covers_total():
    breakdown = mc.compute_margin_breakdown(
        notional=8.0, leverage=1.0, available_margin=100.0,
        taker_fee_rate=0.0004, maint_margin_ratio=0.004, maint_cum=0.0,
        safety_buffer_usdt=1.0, safety_buffer_pct=0.0,
    )
    assert breakdown.status == "PASS"
    assert breakdown.missing == 0.0


def test_margin_breakdown_fail_reports_exact_missing_amount():
    # matches the real production scenario this feature was built for:
    # $80 notional at 1x leverage needs $80 margin alone against a $10 wallet
    breakdown = mc.compute_margin_breakdown(
        notional=80.0, leverage=1.0, available_margin=10.0,
        taker_fee_rate=0.0004, maint_margin_ratio=0.004, maint_cum=0.0,
        safety_buffer_usdt=1.0, safety_buffer_pct=0.0,
    )
    assert breakdown.status == "FAIL"
    assert breakdown.missing == pytest.approx(breakdown.total_required - 10.0)
    assert breakdown.missing > 0


def test_safety_buffer_uses_the_larger_of_flat_or_percentage():
    breakdown = mc.compute_margin_breakdown(
        notional=10.0, leverage=1.0, available_margin=1000.0,
        taker_fee_rate=0.0, maint_margin_ratio=0.0, maint_cum=0.0,
        safety_buffer_usdt=1.0, safety_buffer_pct=0.10,
    )
    # 10% of 1000 available margin (100) dominates the flat $1 floor
    assert breakdown.safety_buffer == pytest.approx(100.0)


# --------------------------------------------------- recommended max notional

def test_recommended_max_notional_produces_a_passing_breakdown():
    max_notional, reserved = mc.recommended_max_notional(
        available_margin=10.0, leverage=3.0, taker_fee_rate=0.0004,
        maint_margin_ratio=0.004, safety_buffer_usdt=1.0, safety_buffer_pct=0.0,
    )
    assert max_notional > 0
    assert reserved == pytest.approx(1.0)

    # the recommended notional's own breakdown must itself PASS (round-trip check)
    breakdown = mc.compute_margin_breakdown(
        notional=max_notional, leverage=3.0, available_margin=10.0,
        taker_fee_rate=0.0004, maint_margin_ratio=0.004, maint_cum=0.0,
        safety_buffer_usdt=1.0, safety_buffer_pct=0.0,
    )
    assert breakdown.status == "PASS"
    assert breakdown.missing == pytest.approx(0.0, abs=1e-6)


def test_recommended_max_notional_is_zero_when_buffer_exceeds_balance():
    max_notional, reserved = mc.recommended_max_notional(
        available_margin=0.50, leverage=3.0, taker_fee_rate=0.0004,
        maint_margin_ratio=0.004, safety_buffer_usdt=1.0, safety_buffer_pct=0.0,
    )
    assert max_notional == 0.0
    assert reserved == 1.0


# ----------------------------------------------------------- max tradable qty

def test_max_tradable_quantity_respects_min_qty_floor():
    qty, notional = mc.max_tradable_quantity(
        available_margin=10.0, price=50000.0, leverage=1.0, step_size=0.001, precision=3, min_qty=0.001,
    )
    # 10 USDT / 50000 = 0.0002 BTC, rounds below the 0.001 minimum -> zero
    assert qty == 0.0
    assert notional == 0.0


def test_max_tradable_quantity_scales_with_leverage():
    qty, notional = mc.max_tradable_quantity(
        available_margin=10.0, price=50000.0, leverage=100.0, step_size=0.001, precision=3, min_qty=0.001,
    )
    assert qty == pytest.approx(0.02, abs=1e-9)
    assert notional == pytest.approx(1000.0, abs=1e-6)


# ------------------------------------------------------------- liquidation

def test_estimate_liquidation_price_long_is_below_entry():
    liq = mc.estimate_liquidation_price(entry_price=50000.0, leverage=10.0, maint_margin_ratio=0.004, side="LONG")
    assert liq < 50000.0
    assert liq == pytest.approx(50000.0 * (1 - 0.1 + 0.004))


def test_estimate_liquidation_price_short_is_above_entry():
    liq = mc.estimate_liquidation_price(entry_price=50000.0, leverage=10.0, maint_margin_ratio=0.004, side="SHORT")
    assert liq > 50000.0
    assert liq == pytest.approx(50000.0 * (1 + 0.1 - 0.004))


def test_estimate_liquidation_price_never_negative_for_long_at_high_leverage():
    liq = mc.estimate_liquidation_price(entry_price=100.0, leverage=125.0, maint_margin_ratio=0.004, side="LONG")
    assert liq >= 0.0


# ---------------------------------------------------------------- risk gauge

def test_risk_capacity_score_averages_available_components_only():
    score, level = mc.risk_capacity_score({"a": 100.0, "b": 50.0, "c": None})
    assert score == 75.0
    assert level == "GREEN"


def test_risk_capacity_score_defaults_to_neutral_when_nothing_available():
    score, level = mc.risk_capacity_score({"a": None, "b": None})
    assert score == 50.0
    assert level == "YELLOW"


@pytest.mark.parametrize("score,level", [(90, "GREEN"), (60, "YELLOW"), (30, "ORANGE"), (5, "RED")])
def test_risk_capacity_level_thresholds(score, level):
    _, computed_level = mc.risk_capacity_score({"x": float(score)})
    assert computed_level == level
