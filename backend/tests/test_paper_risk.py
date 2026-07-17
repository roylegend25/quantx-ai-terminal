"""Margin, liquidation and live TP/SL editing for paper positions.

Covers the calculation module (app/trading/margin.py) directly plus the
API surface: PATCH /positions/{id}/risk validation for both sides,
ownership/closed-position rejection, TP/SL/liquidation auto-close on
/positions reads, duplicate-close prevention and partial closes.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.paper as paper_module
from app.db.session import SessionLocal
from app.db.models import Trade
from app.trading import margin as margin_calc
from app.trading.position_manager import should_close_position
from app.core.security import create_access_token, create_internal_service_token


def make_client(monkeypatch, prices, user=None):
    """Bare paper router with a scripted price feed. `prices` values are
    yielded in order; the last one repeats forever."""
    seq = list(prices)

    async def fake_get_price(symbol: str) -> float:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(paper_module, "get_price", fake_get_price)

    app = FastAPI()
    app.include_router(paper_module.router)
    if user is not None:
        app.dependency_overrides[paper_module.get_optional_user] = lambda: user
    return TestClient(app, headers={"Authorization":f"Bearer {create_internal_service_token()}"})


# ---------------------------------------------------------------- formulas

def test_long_liquidation_price():
    # entry 1000, 5x, mmr 0.5%: 1000 * (1 - 0.2) / (1 - 0.005) = 804.02
    liq = margin_calc.estimated_liquidation_price("LONG", 1000.0, 5.0, 0.005)
    assert liq == pytest.approx(1000 * 0.8 / 0.995)
    assert liq < 1000


def test_short_liquidation_price():
    # entry 1000, 5x, mmr 0.5%: 1000 * (1 + 0.2) / (1 + 0.005) = 1194.03
    liq = margin_calc.estimated_liquidation_price("SHORT", 1000.0, 5.0, 0.005)
    assert liq == pytest.approx(1000 * 1.2 / 1.005)
    assert liq > 1000


def test_long_1x_has_no_meaningful_liquidation():
    assert margin_calc.estimated_liquidation_price("LONG", 1000.0, 1.0, 0.005) is None


def test_short_1x_liquidation_is_real():
    # a 1x SHORT is liquidated when price roughly doubles - meaningful
    liq = margin_calc.estimated_liquidation_price("SHORT", 1000.0, 1.0, 0.005)
    assert liq == pytest.approx(2000 / 1.005)


def test_liquidation_null_without_leverage():
    assert margin_calc.estimated_liquidation_price("LONG", 1000.0, None) is None


def test_margin_calculation_on_open(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0])
    r = client.post("/api/paper/open", params={"symbol": "BTCUSDT", "side": "LONG", "usdt_size": 1000, "leverage": 5})
    assert r.status_code == 200
    t = r.json()["trade"]
    assert t["leverage"] == 5
    assert t["margin_used"] == pytest.approx(200.0)
    assert t["margin_mode"] == "isolated"
    assert t["liquidation_price"] == pytest.approx(round(100 * 0.8 / 0.995, 2))


def test_positions_payload_schema_complete(monkeypatch):
    """The mobile app consumes the same payload - every documented field
    must be present on each position row."""
    client = make_client(monkeypatch, prices=[100.0, 101.0])
    client.post("/api/paper/open", params={"symbol": "ETHUSDT", "side": "SHORT", "usdt_size": 1000, "leverage": 5, "sl": 110, "tp": 90})
    pos = client.get("/api/paper/positions").json()["positions"][0]

    for field in [
        "id", "symbol", "side", "quantity", "entry_price", "mark_price",
        "leverage", "margin_mode", "notional_value", "margin_used",
        "initial_margin", "maintenance_margin", "liquidation_price",
        "distance_to_liquidation_pct", "stop_loss", "take_profit",
        "risk_reward_ratio", "unrealized_pnl", "unrealized_pnl_pct",
        "opened_at", "updated_at", "trailing_stop", "effective_leverage",
    ]:
        assert field in pos, f"missing field {field}"

    assert pos["margin_used"] == pytest.approx(200.0)
    assert pos["notional_value"] == pytest.approx(10 * 101.0)
    # SHORT 5x from 100: liq = 100 * 1.2 / 1.005 = 119.40 (> mark)
    assert pos["liquidation_price"] == pytest.approx(round(100 * 1.2 / 1.005, 2))
    assert pos["distance_to_liquidation_pct"] > 0
    # rr = |90-100| / |110-100| = 1.0
    assert pos["risk_reward_ratio"] == pytest.approx(1.0)
    # pnl = (100-101)*10 = -10 on 200 margin -> -5%
    assert pos["unrealized_pnl"] == pytest.approx(-10.0)
    assert pos["unrealized_pnl_pct"] == pytest.approx(-5.0)


def test_manual_open_is_honestly_recorded_as_manual(monkeypatch):
    """A direct API/UI open (no decision_engine context) must never be
    fabricated as an automated Trading Horizon entry."""
    client = make_client(monkeypatch, prices=[100.0, 100.0])
    client.post("/api/paper/open", params={"symbol": "BTCUSDT", "side": "LONG", "usdt_size": 1000, "leverage": 5})
    pos = client.get("/api/paper/positions").json()["positions"][0]
    assert pos["execution_mode"] == "manual"
    assert pos["decision_id"] is None
    assert pos["authority_id"] is None
    assert pos["edge_at_entry"] is None


def test_real_user_can_still_open_a_manual_trade(monkeypatch):
    """The frontend's Open Long/Short buttons authenticate as a normal
    logged-in user, never the internal-service token - this must keep
    working exactly as it did before Trading Horizon existed. Regression
    test: /api/paper/open must not universally require the internal
    service token, only for requests that actually claim automated
    provenance (see test_external_caller_cannot_fake_automated_provenance)."""
    client = make_client(monkeypatch, prices=[100.0, 100.0])
    client.headers["Authorization"] = f"Bearer {create_access_token(subject='a-real-logged-in-user')}"
    r = client.post("/api/paper/open", params={"symbol": "BTCUSDT", "side": "LONG", "usdt_size": 1000, "leverage": 5})
    assert r.status_code == 200
    pos = client.get("/api/paper/positions").json()["positions"][0]
    assert pos["execution_mode"] == "manual"


def test_external_caller_cannot_fake_automated_provenance(monkeypatch):
    """A non-internal caller claiming Trading Horizon provenance (here, a
    decision_id) must still be rejected - only the actual claim is gated,
    not every manual open."""
    client = make_client(monkeypatch, prices=[100.0, 100.0])
    client.headers["Authorization"] = f"Bearer {create_access_token(subject='a-real-logged-in-user')}"
    r = client.post("/api/paper/open", params={"symbol": "BTCUSDT", "side": "LONG", "usdt_size": 1000, "leverage": 5},
                     json={"decision_id": "forged-decision-id"})
    assert r.status_code == 403
    assert r.json()["detail"] == "HORIZON_AUTHORITY_REQUIRED"


def test_legacy_position_reports_nulls_with_reasons(monkeypatch):
    """Rows opened before leverage tracking must return null margin fields
    with an explanation - never fabricated values."""
    client = make_client(monkeypatch, prices=[100.0])
    db = SessionLocal()
    t = Trade(symbol="BTCUSDT", side="LONG", entry=90.0, qty=1.0, status="OPEN")
    db.add(t)
    db.commit()
    db.close()

    pos = client.get("/api/paper/positions").json()["positions"][0]
    assert pos["leverage"] is None
    assert pos["margin_used"] is None
    assert pos["liquidation_price"] is None
    assert pos["field_notes"]["leverage"]
    assert pos["field_notes"]["liquidation_price"].startswith("cannot estimate")
    # PnL% still real: computed on the entry notional the row actually committed
    assert pos["unrealized_pnl"] == pytest.approx(10.0)
    assert pos["unrealized_pnl_pct"] == pytest.approx(10 / 90 * 100, rel=1e-3)


# ------------------------------------------------------------ TP/SL editing

def _open(client, side="LONG", sl=None, tp=None, leverage=5, symbol="BTCUSDT"):
    params = {"symbol": symbol, "side": side, "usdt_size": 1000, "leverage": leverage}
    if sl is not None:
        params["sl"] = sl
    if tp is not None:
        params["tp"] = tp
    r = client.post("/api/paper/open", params=params)
    assert r.status_code == 200, r.text
    return r.json()["trade"]["id"]


def test_valid_long_risk_update(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0])
    tid = _open(client, "LONG")
    r = client.patch(f"/api/paper/positions/{tid}/risk", json={"stop_loss": 95.0, "take_profit": 112.0})
    assert r.status_code == 200, r.text
    pos = r.json()["position"]
    assert pos["stop_loss"] == 95.0
    assert pos["take_profit"] == 112.0
    assert pos["risk_reward_ratio"] == pytest.approx(12 / 5)


def test_invalid_long_risk_update(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0])
    tid = _open(client, "LONG")
    # SL above current price is invalid for a LONG
    r = client.patch(f"/api/paper/positions/{tid}/risk", json={"stop_loss": 105.0})
    assert r.status_code == 400
    body = r.json()
    assert body["field"] == "stop_loss"
    assert "LONG" in body["detail"]
    # TP below current price is invalid for a LONG
    r = client.patch(f"/api/paper/positions/{tid}/risk", json={"take_profit": 95.0})
    assert r.status_code == 400
    assert r.json()["field"] == "take_profit"


def test_valid_short_risk_update(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0])
    tid = _open(client, "SHORT")
    r = client.patch(f"/api/paper/positions/{tid}/risk", json={"stop_loss": 104.0, "take_profit": 92.0})
    assert r.status_code == 200, r.text
    pos = r.json()["position"]
    assert pos["stop_loss"] == 104.0
    assert pos["take_profit"] == 92.0


def test_invalid_short_risk_update(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0])
    tid = _open(client, "SHORT")
    r = client.patch(f"/api/paper/positions/{tid}/risk", json={"stop_loss": 96.0})
    assert r.status_code == 400
    assert r.json()["field"] == "stop_loss"
    r = client.patch(f"/api/paper/positions/{tid}/risk", json={"take_profit": 103.0})
    assert r.status_code == 400
    assert r.json()["field"] == "take_profit"


def test_rejects_zero_negative_and_too_close(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0])
    tid = _open(client, "LONG")
    for bad in (0, -5):
        r = client.patch(f"/api/paper/positions/{tid}/risk", json={"stop_loss": bad})
        assert r.status_code in (400, 422)
    # within the 0.05% minimum distance of mark 100
    r = client.patch(f"/api/paper/positions/{tid}/risk", json={"stop_loss": 99.99})
    assert r.status_code == 400
    assert "too close" in r.json()["detail"]


def test_rejects_tp_equal_sl(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0])
    tid = _open(client, "LONG")
    r = client.patch(f"/api/paper/positions/{tid}/risk", json={"stop_loss": 95.0, "take_profit": 95.0})
    assert r.status_code == 400


def test_closed_position_cannot_be_edited(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0])
    tid = _open(client, "LONG")
    client.post(f"/api/paper/close/{tid}")
    r = client.patch(f"/api/paper/positions/{tid}/risk", json={"stop_loss": 95.0})
    assert r.status_code == 400
    assert "closed" in r.json()["detail"].lower()


def test_other_user_cannot_edit_position(monkeypatch):
    # opened by alice...
    client_alice = make_client(monkeypatch, prices=[100.0], user="alice")
    tid = _open(client_alice, "LONG")
    # ...edited by bob
    client_bob = make_client(monkeypatch, prices=[100.0], user="bob")
    r = client_bob.patch(f"/api/paper/positions/{tid}/risk", json={"stop_loss": 95.0})
    assert r.status_code == 403
    # owner still can
    r = client_alice.patch(f"/api/paper/positions/{tid}/risk", json={"stop_loss": 95.0})
    assert r.status_code == 200


# ------------------------------------------------------------- auto close

def test_tp_auto_close_long(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0, 111.0])
    tid = _open(client, "LONG", sl=95, tp=110)
    positions = client.get("/api/paper/positions").json()["positions"]
    assert positions == []

    db = SessionLocal()
    t = db.get(Trade, tid)
    assert t.status == "CLOSED"
    assert t.close_reason == "TAKE_PROFIT"
    assert t.pnl > 0
    db.close()


def test_sl_auto_close_short(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0, 105.0])
    tid = _open(client, "SHORT", sl=104, tp=90)
    assert client.get("/api/paper/positions").json()["positions"] == []

    db = SessionLocal()
    t = db.get(Trade, tid)
    assert t.status == "CLOSED"
    assert t.close_reason == "STOP_LOSS"
    assert t.pnl < 0
    db.close()


def test_liquidation_auto_close(monkeypatch):
    # LONG 5x from 100 -> est. liq 80.40; mark gaps to 75, no SL set
    client = make_client(monkeypatch, prices=[100.0, 75.0])
    tid = _open(client, "LONG", leverage=5)
    assert client.get("/api/paper/positions").json()["positions"] == []

    db = SessionLocal()
    t = db.get(Trade, tid)
    assert t.status == "CLOSED"
    assert t.close_reason == "LIQUIDATION"
    # filled at the estimated liquidation level, not the gapped mark
    assert t.exit == pytest.approx(100 * 0.8 / 0.995)
    db.close()


def test_should_close_reason_codes():
    assert should_close_position("LONG", 89.0, 90.0, 120.0) == (True, "STOP_LOSS")
    assert should_close_position("LONG", 121.0, 90.0, 120.0) == (True, "TAKE_PROFIT")
    assert should_close_position("SHORT", 121.0, 120.0, 90.0) == (True, "STOP_LOSS")
    assert should_close_position("SHORT", 89.0, 120.0, 90.0) == (True, "TAKE_PROFIT")
    assert should_close_position("LONG", 79.0, None, None, liquidation_price=80.0) == (True, "LIQUIDATION")
    assert should_close_position("LONG", 89.0, 90.0, None, trailing=True) == (True, "TRAILING_STOP")
    assert should_close_position("LONG", 100.0, 90.0, 120.0) == (False, "")


def test_duplicate_close_prevention(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0, 105.0])
    tid = _open(client, "LONG")

    first = client.post(f"/api/paper/close/{tid}")
    assert first.status_code == 200
    second = client.post(f"/api/paper/close/{tid}")
    assert second.status_code == 400

    # core guard itself: a stale in-memory object can't double-book PnL
    db = SessionLocal()
    t = db.get(Trade, tid)
    assert paper_module._close_trade_core(t, 200.0, db, reason="MANUAL") is None
    db.close()


def test_trailing_stop_ratchets_sl_up(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0, 108.0])
    tid = _open(client, "LONG")
    r = client.patch(f"/api/paper/positions/{tid}/risk", json={"trailing_stop": 3.0})
    assert r.status_code == 200, r.text

    # next read at mark 108: SL should ratchet to 105
    pos = client.get("/api/paper/positions").json()["positions"][0]
    assert pos["stop_loss"] == pytest.approx(105.0)
    assert pos["trailing_stop"] == 3.0


# ------------------------------------------------------------ partial close

def test_partial_close_updates_remaining_and_realized(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0, 110.0])
    tid = _open(client, "LONG", leverage=5)  # qty 10, margin 200

    r = client.post(f"/api/paper/positions/{tid}/close", json={"quantity": 4.0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pnl"] == pytest.approx(40.0)  # (110-100)*4
    assert body["remaining_quantity"] == pytest.approx(6.0)

    db = SessionLocal()
    t = db.get(Trade, tid)
    assert t.status == "OPEN"
    assert t.qty == pytest.approx(6.0)
    assert t.margin_used == pytest.approx(120.0)
    assert t.realized_pnl == pytest.approx(40.0)
    # a CLOSED journal row exists for the slice
    slices = db.query(Trade).filter(Trade.close_reason == "PARTIAL_CLOSE").all()
    assert len(slices) == 1
    assert slices[0].qty == pytest.approx(4.0)
    db.close()


def test_partial_close_full_quantity_closes_all(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0, 110.0])
    tid = _open(client, "LONG")
    r = client.post(f"/api/paper/positions/{tid}/close", json={})
    assert r.status_code == 200
    db = SessionLocal()
    assert db.get(Trade, tid).status == "CLOSED"
    db.close()


def test_portfolio_margin_summary(monkeypatch):
    client = make_client(monkeypatch, prices=[100.0])
    _open(client, "LONG", leverage=5)
    p = client.get("/api/paper/portfolio").json()
    assert p["total_margin_used"] == pytest.approx(200.0)
    assert p["total_notional_exposure"] == pytest.approx(1000.0)
    assert p["margin_usage_pct"] == pytest.approx(2.0)  # 200 / 10000
    assert p["open_positions"] == 1
    assert "max_open_positions" in p
    assert p["nearest_liquidation_distance_pct"] > 0
