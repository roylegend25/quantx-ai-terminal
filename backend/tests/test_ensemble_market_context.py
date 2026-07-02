from app.strategy import ensemble

FIXED_STRATEGIES = {
    "trend": {"direction": "LONG", "confidence": 90},
    "momentum": {"direction": "LONG", "confidence": 70},
    "mean_reversion": {"direction": "NO_TRADE", "confidence": 0},
    "breakout": {"direction": "LONG", "confidence": 50},
}


def _fixed_ensemble(monkeypatch):
    monkeypatch.setattr(ensemble.manager, "evaluate", lambda features: FIXED_STRATEGIES)
    monkeypatch.setattr(ensemble, "detect", lambda features: "NORMAL")


def test_no_market_context_leaves_behavior_unchanged(monkeypatch):
    _fixed_ensemble(monkeypatch)
    result = ensemble.evaluate({})
    assert result["market_context_adjustment"] == 0.0
    baseline_confidence = result["ensemble"]["confidence"]

    result_again = ensemble.evaluate({}, market_context=None)
    assert result_again["ensemble"]["confidence"] == baseline_confidence
    assert result_again["ensemble"]["direction"] == result["ensemble"]["direction"]


def test_agreeing_bullish_context_boosts_confidence_without_changing_direction(monkeypatch):
    _fixed_ensemble(monkeypatch)
    baseline = ensemble.evaluate({})
    assert baseline["ensemble"]["direction"] == "LONG"

    boosted = ensemble.evaluate({}, market_context={"bias_score": 0.9})

    assert boosted["ensemble"]["direction"] == "LONG"
    assert boosted["ensemble"]["confidence"] > baseline["ensemble"]["confidence"]
    assert 5 <= boosted["market_context_adjustment"] <= 15


def test_disagreeing_bearish_context_reduces_confidence_without_changing_direction(monkeypatch):
    _fixed_ensemble(monkeypatch)
    baseline = ensemble.evaluate({})
    assert baseline["ensemble"]["direction"] == "LONG"

    penalized = ensemble.evaluate({}, market_context={"bias_score": -0.9})

    assert penalized["ensemble"]["direction"] == "LONG"
    assert penalized["ensemble"]["confidence"] < baseline["ensemble"]["confidence"]
    assert -15 <= penalized["market_context_adjustment"] <= -5


def test_confidence_is_clamped_to_0_100(monkeypatch):
    _fixed_ensemble(monkeypatch)
    penalized = ensemble.evaluate({}, market_context={"bias_score": -1.0})
    assert 0.0 <= penalized["ensemble"]["confidence"] <= 100.0
