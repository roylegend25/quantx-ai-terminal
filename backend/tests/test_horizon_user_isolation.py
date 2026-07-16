from datetime import datetime, timedelta, timezone

from app.db.models import PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine.repository import performance


def test_resolved_performance_is_strictly_user_scoped():
    db=SessionLocal(); db.query(PredictionResolution).delete(); db.query(PredictionLedger).delete()
    now=datetime.now(timezone.utc)
    for index in range(25):
        pid=f"owner-a-{index}"
        db.add(PredictionLedger(prediction_id=pid,candidate_id=f"candidate-{index}",decision_id=f"decision-{index}",
            user_id="owner-a",engine="active_drive_v2",engine_version="2.2.0",source_type="strategy",
            source_name="trend",source_version="2.1.0",symbol="BTCUSDT",timeframe="15m",market_regime="Normal",
            direction="LONG",confidence=.8,points=5,target_horizon_seconds=900,resolution_deadline=now+timedelta(minutes=15),
            feature_snapshot_hash="hash",generated_at=now))
        db.add(PredictionResolution(prediction_id=pid,actual_return=.01,resolved_direction="LONG",correct=True,
            neutral_result=False,resolution_reason="horizon_elapsed",resolved_at=now))
    db.commit()
    owner_a=performance(db,"owner-a","trend","2.1.0","BTCUSDT","15m","Normal")
    owner_b=performance(db,"owner-b","trend","2.1.0","BTCUSDT","15m","Normal")
    db.close()
    assert owner_a["resolved"]==25 and owner_a["realized_edge"]==.01
    assert owner_b["resolved"]==0 and owner_b["realized_edge"] is None
