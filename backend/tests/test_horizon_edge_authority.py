from app.trading_horizon.service import build_horizon_decision


def frames(edge, supported):
    return {tf:{"direction":"LONG","final_signal":"LONG","eligible_for_execution":True,"confidence":.8,
               "expected_edge":edge,"current_edge_supported":supported}
            for tf in ("5m","15m","1h","4h","1d","1w")}


def test_historical_positive_edge_never_fills_missing_current_edge():
    decision=build_horizon_decision("BTCUSDT",frames(None,False),"mid_term",
        historical_edge_summary={"historical_expected_edge":.08,"historical_edge_sample_count":50,
                                 "historical_edge_lower_bound":.03,"authoritative":False})
    assert decision["current_edge"] is None and decision["current_edge_supported"] is False
    assert decision["ready"] is False and decision["direction"]=="NO_TRADE"
    assert "EXPECTED_EDGE_NOT_SUPPORTED" in decision["blockers"]
    assert decision["historical_edge_summary"]["historical_expected_edge"]==.08


def test_historical_lower_bound_cannot_override_unsupported_current_edge():
    decision=build_horizon_decision("BTCUSDT",frames(.02,False),"mid_term",
        historical_edge_summary={"historical_edge_lower_bound":.01,"authoritative":False})
    assert decision["current_edge"]==.02 and decision["ready"] is False
    assert "EXPECTED_EDGE_NOT_SUPPORTED" in decision["blockers"]


def test_supported_positive_current_edge_can_pass_and_negative_cannot():
    positive=build_horizon_decision("BTCUSDT",frames(.02,True),"mid_term")
    assert positive["ready"] is True and positive["direction"]=="LONG"
    negative=build_horizon_decision("BTCUSDT",frames(-.02,True),"mid_term",
        historical_edge_summary={"historical_expected_edge":.10,"authoritative":False})
    assert negative["ready"] is False and negative["current_edge"]==-.02

