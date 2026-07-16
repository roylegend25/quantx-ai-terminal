from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db.models import ExchangePositionRow
from app.db.session import SessionLocal
from app.main import app


def test_profile_is_authenticated_user_scoped_revisioned_and_future_only():
    client=TestClient(app); headers={"Authorization":f"Bearer {create_access_token('profile-user')}"}
    assert client.get("/api/bot/trading-profile").status_code==401
    initial=client.get("/api/bot/trading-profile",headers=headers).json()
    assert initial["trading_profile"]=="auto_adaptive" and initial["strict_timeframe_unanimity"] is True
    db=SessionLocal(); db.add(ExchangePositionRow(mode="PAPER",symbol="BTCUSDT",side="LONG",quantity=1,entry_price=100)); db.commit(); db.close()
    payload={"trading_profile":"mid_term","strict_timeframe_unanimity":True,"auto_profile_enabled":False,"expected_revision":initial["profile_revision"]}
    saved=client.patch("/api/bot/trading-profile",headers=headers,json=payload)
    assert saved.status_code==200 and saved.json()["profile_revision"]==initial["profile_revision"]+1
    conflict=client.patch("/api/bot/trading-profile",headers=headers,json=payload)
    assert conflict.status_code==409
    invalid=client.patch("/api/bot/trading-profile",headers=headers,json={**payload,"trading_profile":"scalp","expected_revision":saved.json()["profile_revision"]})
    assert invalid.status_code==422
    non_strict=client.patch("/api/bot/trading-profile",headers=headers,json={**payload,
        "strict_timeframe_unanimity":False,"expected_revision":saved.json()["profile_revision"]})
    assert non_strict.status_code==422 and non_strict.json()["detail"]=="STRICT_TIMEFRAME_UNANIMITY_REQUIRED"
    db=SessionLocal(); position=db.query(ExchangePositionRow).filter_by(mode="PAPER",symbol="BTCUSDT").first(); assert position and position.entry_price==100; db.close()
