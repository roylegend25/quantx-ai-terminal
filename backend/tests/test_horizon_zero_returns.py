from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.db.models import PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine.repository import performance
from app.trading_horizon.service import previous_oos_comparison


@pytest.mark.parametrize(("actual","fallback","expected"),[
    (0.0,None,0.0),(0.0,.4,0.0),(None,.4,.4),(-.3,.4,-.3),(Decimal("0"),None,Decimal("0")),
])
def test_resolved_return_fallback_uses_none_not_truthiness(actual,fallback,expected):
    result=previous_oos_comparison([{"prediction_id":"p","direction":"LONG","outcome":"NEUTRAL",
        "actual_return":actual,"return_pct":fallback}],"LONG")
    assert result["actual_return"]==expected


def test_flat_outcome_counts_for_edge_but_not_win_or_loss_denominator():
    user="flat-policy-user"; db=SessionLocal()
    db.query(PredictionResolution).filter(PredictionResolution.prediction_id.like("flat-policy-%")).delete(synchronize_session=False)
    db.query(PredictionLedger).filter_by(user_id=user).delete(); now=datetime.now(timezone.utc)
    samples=[("win",.3,True),("flat",0.0,None),("loss",-.3,False)]
    for suffix,value,correct in samples:
        pid=f"flat-policy-{suffix}"
        db.add(PredictionLedger(prediction_id=pid,candidate_id=f"c-{pid}",decision_id=f"d-{pid}",user_id=user,
            engine="active_drive_v2",engine_version="2.2.0",source_type="strategy",source_name="trend",
            source_version="2.1.0",symbol="BTCUSDT",timeframe="15m",market_regime="Normal",direction="LONG",
            confidence=.8,points=5,target_horizon_seconds=900,resolution_deadline=now+timedelta(minutes=15),
            feature_snapshot_hash="hash",generated_at=now))
        db.add(PredictionResolution(prediction_id=pid,actual_return=value,resolved_direction="LONG",correct=correct,
            neutral_result=correct is None,resolution_reason="resolved",resolved_at=now))
    db.commit(); result=performance(db,user,"trend","2.1.0","BTCUSDT","15m","Normal"); db.close()
    assert result["resolved"]==3 and result["directional_resolved"]==2 and result["neutral_resolved"]==1
    assert result["accuracy"]==.5
    assert result["realized_edge"]==pytest.approx(0.0)
    assert result["average_win_return"]==.3 and result["average_loss_return"]==-.3
