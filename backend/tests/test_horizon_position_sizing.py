import pytest

from app.db.models import Portfolio, Trade
from app.db.session import SessionLocal
from app.trading_horizon import sizing


def _approval(user="user-a", limit=600.0, stop_pct=None):
    return {"user_id":user,"symbol":"BTCUSDT","approved_notional_ceiling_usd":limit,
            "stop_distance_pct":stop_pct,"risk_policy_revision":"r1"}


def _risk(limit, pct=10.0, owner="user-a", max_positions=5):
    return {"max_position_size_usd":limit,"max_risk_per_trade_pct":pct,
            "max_open_positions":max_positions,"updated_at":"r2","risk_policy_owner":owner}


def _portfolio(equity=10_000):
    db=SessionLocal(); db.add(Portfolio(id=1,balance=equity,equity=equity)); db.commit(); return db


def test_paper_uses_user_and_authority_not_binance_cap(monkeypatch):
    db=_portfolio(); monkeypatch.setattr(sizing.settings_repository,"get_user_settings",lambda user,scope=None,db=None:_risk(500,owner=user))
    monkeypatch.setattr(sizing.settings,"binance_max_notional_per_trade",25.0)
    result=sizing.calculate_position_size(db,user_id="user-a",symbol="BTCUSDT",risk_approval=_approval(),mode="PAPER")
    assert result["final_approved_notional_usd"]==500.0
    assert result["exchange_limit_usd"] is None
    db.close()


@pytest.mark.parametrize("current,persisted,mode,exchange,expected",[
    (20,600,"BINANCE_LIVE",250,20),
    (1000,600,"BINANCE_LIVE",250,250),
    (500,100,"PAPER",25,100),
    (100,500,"PAPER",25,100),
])
def test_most_restrictive_ceiling_wins(monkeypatch,current,persisted,mode,exchange,expected):
    db=_portfolio(); monkeypatch.setattr(sizing.settings_repository,"get_user_settings",lambda user,scope=None,db=None:_risk(current,owner=user))
    monkeypatch.setattr(sizing.settings,"binance_max_notional_per_trade",exchange)
    result=sizing.calculate_position_size(db,user_id="user-a",symbol="BTCUSDT",
        risk_approval=_approval(limit=persisted),mode=mode)
    assert result["final_approved_notional_usd"]==expected
    db.close()


def test_stop_distance_and_exposure_reduce_size(monkeypatch):
    db=_portfolio(10_000); monkeypatch.setattr(sizing.settings_repository,"get_user_settings",lambda user,scope=None,db=None:_risk(1000,pct=1,owner=user))
    result=sizing.calculate_position_size(db,user_id="user-a",symbol="BTCUSDT",
        risk_approval=_approval(limit=1000,stop_pct=20),mode="PAPER")
    assert result["risk_based_limit_usd"]==500 and result["final_approved_notional_usd"]==500
    db.add(Trade(symbol="BTCUSDT",user_id="user-a",status="OPEN",entry=100,qty=8,side="LONG")); db.commit()
    result=sizing.calculate_position_size(db,user_id="user-a",symbol="BTCUSDT",
        risk_approval=_approval(limit=1000),mode="PAPER")
    assert result["symbol_exposure_remaining_usd"]==200
    assert result["final_approved_notional_usd"]==200
    db.close()


def test_user_scope_and_missing_policy_fail_closed(monkeypatch):
    db=_portfolio()
    monkeypatch.setattr(sizing.settings_repository,"get_user_settings",
        lambda user,scope=None,db=None:_risk(1000 if user=="user-a" else 50,owner=user))
    result=sizing.calculate_position_size(db,user_id="user-b",symbol="BTCUSDT",
        risk_approval=_approval(user="user-b",limit=600),mode="PAPER")
    assert result["final_approved_notional_usd"]==50
    with pytest.raises(sizing.PositionSizingError,match="RISK_POLICY_UNAVAILABLE"):
        sizing.calculate_position_size(db,user_id="user-b",symbol="BTCUSDT",
            risk_approval=_approval(user="user-a"),mode="PAPER")
    monkeypatch.setattr(sizing.settings_repository,"get_user_settings",lambda user,scope=None,db=None:(_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(sizing.PositionSizingError,match="RISK_POLICY_UNAVAILABLE"):
        sizing.calculate_position_size(db,user_id="user-a",symbol="BTCUSDT",risk_approval=_approval(),mode="PAPER")
    db.close()


def test_zero_and_quantity_rounding_fail_closed(monkeypatch):
    db=_portfolio(); monkeypatch.setattr(sizing.settings_repository,"get_user_settings",lambda user,scope=None,db=None:_risk(0,owner=user))
    with pytest.raises(sizing.PositionSizingError,match="POSITION_SIZE_LIMIT_ZERO"):
        sizing.calculate_position_size(db,user_id="user-a",symbol="BTCUSDT",risk_approval=_approval(),mode="PAPER")
    qty,actual=sizing.floor_quantity_to_step(100,33,0.1)
    assert actual<=100 and qty==3.0
    db.close()
