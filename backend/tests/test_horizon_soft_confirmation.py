"""PAPER-mode-only soft multi-timeframe confirmation policy.

mid_term profile: execution_timeframe=15m, confirmation (higher) timeframes
=[1h, 4h], structural_bias_timeframe=1d. Confidence fields are the real
production 0-1 fractional scale (matching app.decision_engine.v2's output),
not the 0-100 scale some older strict-path fixtures use - the strict path
never compared confidence magnitude at this layer, but soft confirmation
does (that's the whole point), so getting the scale right here matters.
"""
from app.core.config import settings
from app.trading_horizon.service import build_horizon_decision

REQUIRED_CONFIDENCE = settings.active_drive_min_confidence  # 0.60


def _tf(direction="LONG", confidence=0.75, edge=.02, eligible=None, stale=False, error=None):
    if error:
        return {"error": error}
    if eligible is None:
        eligible = direction in ("LONG", "SHORT")
    return {"direction": direction, "eligible_for_execution": eligible, "confidence": confidence,
            "decision_confidence": confidence, "expected_edge": edge, "current_edge_supported": True,
            "data_status": {"stale": stale}}


def _frames(primary_direction="LONG", primary_confidence=0.75, higher_1h=None, higher_4h=None, bias=None):
    return {
        "15m": _tf(primary_direction, primary_confidence),
        "1h": higher_1h if higher_1h is not None else _tf("NO_TRADE", None, eligible=False),
        "4h": higher_4h if higher_4h is not None else _tf("NO_TRADE", None, eligible=False),
        "1d": bias if bias is not None else _tf("NO_TRADE", None, eligible=False),
    }


def _decide(frames, **kwargs):
    return build_horizon_decision("BTCUSDT", frames, "mid_term", soft_confirmation=True, **kwargs)


def test_full_higher_timeframe_agreement_confirms_with_no_penalty():
    frames = _frames(higher_1h=_tf("LONG", 0.70), higher_4h=_tf("LONG", 0.65))
    d = _decide(frames)
    assert d["primary_timeframe_pass"] is True
    assert d["higher_timeframe_direction"] == ["LONG", "LONG"]
    assert d["higher_timeframe_confidence"] == [0.70, 0.65]
    assert d["confirmation_result"] == "AGREED"
    assert d["confirmation_penalty"] == 0.0
    assert d["direction"] == "LONG"
    assert d["ready"] is True
    assert d["blockers"] == []


def test_one_agreeing_one_neutral_still_confirms_with_small_penalty():
    frames = _frames(higher_1h=_tf("LONG", 0.70), higher_4h=_tf("NO_TRADE", None, eligible=False))
    d = _decide(frames)
    assert d["confirmation_result"] == "AGREED"
    assert 0.0 < d["confirmation_penalty"] < 0.25
    assert d["direction"] == "LONG"
    assert d["ready"] is True
    assert d["confirmed_confidence"] == round(0.75 * (1 - d["confirmation_penalty"]), 4)


def test_strong_higher_timeframe_opposition_rejects_regardless_of_the_other():
    # 1h strongly opposes (SHORT at >= the required confidence bar); 4h
    # agrees - the reject must win even though one higher timeframe agrees.
    frames = _frames(higher_1h=_tf("SHORT", 0.80), higher_4h=_tf("LONG", 0.70))
    d = _decide(frames)
    assert d["confirmation_result"] == "REJECTED"
    assert d["confirmation_penalty"] == 1.0
    assert d["direction"] == "NO_TRADE"
    assert d["ready"] is False
    assert any("strong" in b.lower() and "1h" in b for b in d["blockers"])


def test_weak_opposition_below_threshold_does_not_reject():
    # Opposing direction but confidence below the required bar is "weak",
    # not "strong" - must not hard-reject on its own.
    frames = _frames(higher_1h=_tf("SHORT", 0.40), higher_4h=_tf("LONG", 0.70))
    d = _decide(frames)
    assert d["confirmation_result"] == "AGREED"
    assert d["direction"] == "LONG"
    assert d["ready"] is True


def test_both_higher_timeframes_neutral_applies_penalty_but_does_not_reject():
    frames = _frames(higher_1h=_tf("NO_TRADE", None, eligible=False), higher_4h=_tf("NO_TRADE", None, eligible=False))
    d = _decide(frames)
    assert d["confirmation_result"] == "NEUTRAL"
    assert d["confirmation_penalty"] > 0
    assert d["direction"] == "LONG"  # not auto-rejected
    assert d["ready"] is True
    assert d["blockers"] == []


def test_stale_higher_timeframe_data_does_not_reject():
    frames = _frames(higher_1h=_tf("LONG", 0.55, stale=True), higher_4h=_tf("NO_TRADE", None, eligible=False))
    d = _decide(frames)
    # Stale data cannot itself confirm or oppose - treated as non-confirming.
    assert d["confirmation_result"] == "NEUTRAL"
    assert d["direction"] == "LONG"
    assert d["ready"] is True
    assert "stale" in d["confirmation_reason"].lower()


def test_missing_higher_timeframe_data_does_not_reject():
    frames = _frames(higher_1h=_tf(error="TIMEFRAME_EVALUATION_TIMEOUT"), higher_4h=_tf("NO_TRADE", None, eligible=False))
    d = _decide(frames)
    assert d["confirmation_result"] == "NEUTRAL"
    assert d["ready"] is True


def test_primary_below_threshold_rejects_even_with_full_higher_timeframe_agreement():
    # Soft confirmation never rescues a primary that fails its own 0.60 bar.
    frames = _frames(primary_confidence=0.59, higher_1h=_tf("LONG", 0.90), higher_4h=_tf("LONG", 0.90))
    d = _decide(frames)
    assert d["primary_timeframe_pass"] is False
    assert d["direction"] == "NO_TRADE"
    assert d["ready"] is False
    assert any("0.60" in b or "60%" in b for b in d["blockers"])


def test_long_reachable_under_soft_confirmation():
    frames = _frames("LONG", 0.75, higher_1h=_tf("LONG", 0.70), higher_4h=_tf("LONG", 0.65))
    d = _decide(frames)
    assert d["direction"] == "LONG" and d["ready"] is True


def test_short_reachable_under_soft_confirmation():
    frames = _frames("SHORT", 0.75, higher_1h=_tf("SHORT", 0.70), higher_4h=_tf("SHORT", 0.65))
    d = _decide(frames)
    assert d["direction"] == "SHORT" and d["ready"] is True


def test_strict_mode_default_unaffected_by_soft_confirmation_fields():
    """soft_confirmation defaults to False (non-PAPER modes): the original
    strict-unanimity policy must produce byte-for-byte the same gating as
    before this change, with the new fields present but neutral."""
    frames = _frames(higher_1h=_tf("SHORT", 0.90), higher_4h=_tf("LONG", 0.70))  # would AGREE loosely, but strict requires unanimity
    strict = build_horizon_decision("BTCUSDT", frames, "mid_term")
    assert strict["direction"] == "NO_TRADE"
    assert "Required timeframes are not unanimous" in strict["blockers"]
    assert strict["confirmation_result"] == "STRICT_MODE_NOT_APPLICABLE"
    assert strict["soft_confirmation_policy_active"] is False
    assert strict["strict_unanimity_required"] is True
