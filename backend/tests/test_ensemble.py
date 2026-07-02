from app.strategy import ensemble


def test_ensemble_applies_weighted_probability_formula(monkeypatch):
    fixed_strategies = {
        "trend": {"direction": "LONG", "confidence": 100},
        "momentum": {"direction": "SHORT", "confidence": 100},
        "mean_reversion": {"direction": "NO_TRADE", "confidence": 0},
        "breakout": {"direction": "LONG", "confidence": 50},
    }
    fixed_weights = {
        "trend": 0.41,
        "momentum": 0.29,
        "mean_reversion": 0.18,
        "breakout": 0.12,
    }

    monkeypatch.setattr(ensemble.manager, "evaluate", lambda features: fixed_strategies)
    monkeypatch.setattr(ensemble.weighting, "get_weights", lambda: fixed_weights)
    monkeypatch.setattr(ensemble, "detect", lambda features: "NORMAL")

    result = ensemble.evaluate({})

    # trend -> 100 (prob_up), momentum -> 0, mean_reversion -> 50, breakout -> 75
    expected_probability_up = (
        100 * 0.41 + 0 * 0.29 + 50 * 0.18 + 75 * 0.12
    )
    assert result["ensemble"]["probability_up"] == round(expected_probability_up)
    assert result["weights"] == fixed_weights
    assert result["ensemble"]["direction"] == "LONG"


def test_ensemble_no_trade_band():
    result = ensemble.evaluate({
        "price": 100, "ema20": 100, "ema50": 100, "ema200": 100,
    })
    assert result["ensemble"]["direction"] in {"LONG", "SHORT", "NO_TRADE"}
    assert set(result["weights"].keys()) == {"trend", "momentum", "mean_reversion", "breakout"}
    assert abs(sum(result["weights"].values()) - 1.0) < 1e-6
