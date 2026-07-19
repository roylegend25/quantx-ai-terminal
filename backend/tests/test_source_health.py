from datetime import datetime, timedelta, timezone
from app.api.analysis import source_health
from app.db.models import MarketCandle, PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.decision_engine.ledger import persist
from app.decision_engine.resolver import resolve_due_sync
from app.decision_engine.v2 import ActiveDriveV2Engine


def _legacy():
    return {"direction":"LONG","confidence":70,"strategies":{"trend":{"direction":"LONG","confidence":70,"reason":"test"}},"ml_champion":{"used":False},"features":{"price":100,"ema20":101,"ema50":100,"ema200":99,"rsi":60,"macd_hist":.1,"atr":2,"bb_width":.01,"volume":10,"volume_sma20":10},"target":104,"stop":98}


def test_source_health_reports_invocation_and_shadow_truthfully():
    db=SessionLocal()
    try:
        result=ActiveDriveV2Engine().evaluate({"db":db,"symbol":"HEALTHUSDT","timeframe":"15m","legacy":_legacy(),"regime":"TRENDING","data_status":"live","risk_reward_ratio":2})
        persist(db,"health-user",result,100,_legacy()["features"])
    finally: db.close()
    health=source_health("HEALTHUSDT","15m")
    assert health["summary"]["candidates_generated"] > 0
    shadows=[s for s in health["sources"] if s["shadow"]]
    assert shadows and all(s["runtime_status"]=="shadow_not_inferred" for s in shadows)
    assert all(not s["production_eligible"] for s in shadows)
    assert health["ledger"]["resolved"] == 0


def test_resolver_resolves_once_only_after_real_horizon_candle():
    db=SessionLocal()
    try:
        result=ActiveDriveV2Engine().evaluate({"db":db,"symbol":"RESOLVEUSDT","timeframe":"5m","legacy":_legacy(),"regime":"TRENDING","data_status":"live","risk_reward_ratio":2})
        persist(db,"resolve-user",result,100,_legacy()["features"])
        rows=db.query(PredictionLedger).filter_by(symbol="RESOLVEUSDT").all(); now=datetime.now(timezone.utc)
        for row in rows: row.generated_at=now-timedelta(hours=2); row.resolution_deadline=now-timedelta(hours=1)
        db.add(MarketCandle(symbol="RESOLVEUSDT",timeframe="5m",timestamp=int(rows[0].resolution_deadline.timestamp()*1000),open=101,high=102,low=99,close=101,volume=10,provider="test",quality_score=100,interpolated=False)); db.commit()
        assert resolve_due_sync(db,limit=200)["resolved"]==len(rows)
        assert resolve_due_sync(db,limit=200)["resolved"]==0
        assert db.query(PredictionResolution).join(PredictionLedger,PredictionResolution.prediction_id==PredictionLedger.prediction_id).filter(PredictionLedger.symbol=="RESOLVEUSDT").count()==len(rows)
        ids=[row.prediction_id for row in rows]
        db.query(PredictionResolution).filter(PredictionResolution.prediction_id.in_(ids)).delete(synchronize_session=False); db.commit()
    finally: db.close()
