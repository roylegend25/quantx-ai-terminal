from datetime import datetime, timezone

from app.trading_horizon.service import build_horizon_decision


def _frames(direction="LONG", edge=.02):
    return {tf: {"direction": direction, "eligible_for_execution": True, "confidence": 80,
                 "expected_edge": edge, "current_edge_supported": edge is not None}
            for tf in ("5m", "15m", "1h", "4h", "1d", "1w")}


def test_short_term_requires_strict_unanimity_and_one_authority():
    frames = _frames()
    decision = build_horizon_decision("btcusdt", frames, "short_term", price=100, atr=2,
                                      now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert decision["ready"] is True
    assert decision["execution_timeframe"] == "5m"
    assert decision["direction"] == "LONG"
    assert decision["price_invalidation"]["price"] == 97.5
    frames["1h"]["direction"] = "SHORT"
    blocked = build_horizon_decision("BTCUSDT", frames, "short_term")
    assert blocked["direction"] == "NO_TRADE"
    assert "Required timeframes are not unanimous" in blocked["blockers"]


def test_profiles_expose_readiness_edge_oos_and_invalidation():
    decision = build_horizon_decision("ETHUSDT", _frames(), "safe", resolutions=[
        {"prediction_id": "p1", "direction": "LONG", "outcome": "CORRECT", "actual_return": .03}
    ])
    assert decision["execution_timeframe"] == "1h"
    assert decision["estimated_holding_window"] == "3–14 days"
    assert len(decision["edge_comparison"]) == 3
    assert decision["previous_resolved_oos"]["available"] is True
    assert decision["time_invalidation"]["max_seconds"] == 14 * 86400
