"""API layer for the Live Margin Calculator
(GET /api/trading/binance/margin-calculator, GET .../symbol-recommendations,
app/api/trading_control.py). Uses a dedicated mock client (not the shared
MockReadClient) since this feature needs get_exchange_filters/
get_commission_rate/get_leverage_brackets that older tests don't exercise."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.portfolio as portfolio_module
import app.api.prediction as prediction_module
import app.api.trading_control as tc
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import RiskSettings, TradingControl
from app.exchanges.binance_models import BinanceAccountSummary, BinancePosition
from app.exchanges.binance_snapshot_service import snapshot_service
from app.trading import modes

FAKE_KEY = "AKIAFAKEKEY1234567890"
FAKE_SECRET = "supersecretvalue0987654321"


class MarginMockClient:
    configured = True
    read_only = True

    def __init__(self, wallet=10.0, available=10.0, position=None, mark_price=63900.0,
                brackets=None, taker_rate=0.0005, min_notional=100.0):
        self._wallet = wallet
        self._available = available
        self._position = position
        self._mark_price = mark_price
        self._brackets = brackets if brackets is not None else [
            {"bracket": 1, "initialLeverage": 25, "notionalCap": 50000, "notionalFloor": 0,
             "maintMarginRatio": 0.004, "cum": 0.0},
        ]
        self._taker_rate = taker_rate
        self._min_notional = min_notional

    async def get_account_info(self):
        return BinanceAccountSummary(
            total_wallet_balance=self._wallet, available_balance=self._available, total_margin_balance=self._wallet,
            total_unrealized_pnl=0.0, total_initial_margin=0.0, total_maintenance_margin=0.0,
        )

    async def get_positions(self, symbol=None):
        return [self._position] if self._position else []

    async def get_daily_realized_pnl(self):
        return 0.0

    async def get_mark_price(self, symbol):
        return self._mark_price

    async def get_exchange_filters(self, symbol):
        return {
            "min_qty": 0.001, "step_size": 0.001, "quantity_precision": 3,
            "min_notional": self._min_notional, "tick_size": 0.1, "price_precision": 1,
        }

    async def get_commission_rate(self, symbol):
        return {"maker_rate": 0.0002, "taker_rate": self._taker_rate}

    async def get_leverage_brackets(self, symbol):
        return [
            {
                "bracket": b["bracket"], "initial_leverage": b["initialLeverage"],
                "notional_cap": float(b["notionalCap"]), "notional_floor": float(b["notionalFloor"]),
                "maint_margin_ratio": float(b["maintMarginRatio"]), "cum": float(b["cum"]),
            }
            for b in self._brackets
        ]


def eth_position():
    return BinancePosition(
        symbol="ETHUSDT", side="LONG", quantity=0.01, entry_price=3000.0, mark_price=3010.0,
        leverage=2.0, margin_type="isolated", liquidation_price=1500.0,
        unrealized_pnl=0.1, margin_used=15.0, notional=30.1,
    )


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    db = SessionLocal()
    try:
        db.query(TradingControl).delete()
        db.query(RiskSettings).delete()
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(portfolio_module, "_read_client", None)
    snapshot_service.reset()
    monkeypatch.setattr(settings, "binance_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "binance_api_secret", FAKE_SECRET)
    monkeypatch.setattr(settings, "binance_max_leverage", 3.0)
    monkeypatch.setattr(settings, "binance_default_leverage", 1.0)
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 80.0)
    monkeypatch.setattr(settings, "binance_max_daily_loss_usdt", 20.0)
    monkeypatch.setattr(settings, "binance_allowed_symbols", ["BTCUSDT", "ETHUSDT"])
    yield
    modes.set_mode(modes.MODE_PAPER)


def install_client(monkeypatch, **kwargs):
    client = MarginMockClient(**kwargs)
    monkeypatch.setattr(portfolio_module, "_read_client", client)
    return client


def make_client():
    app = FastAPI()
    app.include_router(tc.router)
    return TestClient(app)


def fake_no_data_prediction():
    async def fn(symbol, interval="5m", timeframe=None, limit=220, current_user=None):
        return None
    return fn


# --------------------------------------------------------------- calculator

def test_margin_calculator_unavailable_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")
    client = make_client()
    body = client.get("/api/trading/binance/margin-calculator?symbol=BTCUSDT").json()
    assert body["available"] is False


def test_margin_calculator_returns_full_account_breakdown(monkeypatch):
    install_client(monkeypatch, wallet=10.0, available=10.0)
    monkeypatch.setattr(prediction_module, "prediction", fake_no_data_prediction())
    client = make_client()
    body = client.get("/api/trading/binance/margin-calculator?symbol=BTCUSDT").json()

    assert body["available"] is True
    assert body["account"]["wallet_balance"] == 10.0
    assert body["account"]["available_balance"] == 10.0
    assert body["account"]["margin_mode"] == "N/A"  # no open BTCUSDT position
    assert body["market"]["current_price"] == 63900.0
    assert body["market"]["min_notional"] == 100.0
    assert "maximum_tradable_quantity" in body["position_sizing"]
    assert "recommended_max_notional" in body["recommendation"]
    assert body["breakdown_at_current_setting"]["notional"] == 80.0
    assert body["risk_capacity"]["level"] in ("GREEN", "YELLOW", "ORANGE", "RED")


def test_margin_calculator_never_hides_the_current_setting_breakdown(monkeypatch):
    """Section 3: instead of just 'insufficient margin', the full
    arithmetic behind the current BINANCE_MAX_NOTIONAL_PER_TRADE setting
    must always be present."""
    install_client(monkeypatch, wallet=10.0, available=10.0)
    monkeypatch.setattr(settings, "binance_default_leverage", 1.0)
    monkeypatch.setattr(prediction_module, "prediction", fake_no_data_prediction())
    client = make_client()
    body = client.get("/api/trading/binance/margin-calculator?symbol=BTCUSDT").json()

    breakdown = body["breakdown_at_current_setting"]
    for field in ("notional", "leverage", "initial_margin", "estimated_fees", "maintenance_margin",
                  "safety_buffer", "total_required", "available_margin", "status", "missing"):
        assert field in breakdown
    # $80 notional at 1x leverage needs $80 margin alone against a $10 wallet
    assert breakdown["status"] == "FAIL"
    assert breakdown["missing"] > 0


def test_margin_calculator_reflects_open_position_leverage_and_margin_mode(monkeypatch):
    install_client(monkeypatch, wallet=100.0, available=70.0, position=eth_position())
    monkeypatch.setattr(prediction_module, "prediction", fake_no_data_prediction())
    client = make_client()
    body = client.get("/api/trading/binance/margin-calculator?symbol=ETHUSDT").json()

    assert body["account"]["margin_mode"] == "isolated"
    assert body["account"]["current_leverage"] == 2.0


def test_margin_calculator_uses_real_leverage_brackets_not_a_guess(monkeypatch):
    install_client(monkeypatch, wallet=10.0, available=10.0, brackets=[
        {"bracket": 1, "initialLeverage": 25, "notionalCap": 50000, "notionalFloor": 0,
         "maintMarginRatio": 0.0065, "cum": 0.0},
    ])
    monkeypatch.setattr(prediction_module, "prediction", fake_no_data_prediction())
    client = make_client()
    body = client.get("/api/trading/binance/margin-calculator?symbol=BTCUSDT").json()

    assert body["market"]["maintenance_margin_ratio"] == 0.0065
    assert body["market"]["maintenance_margin_estimated"] is False


def test_margin_calculator_flags_estimated_maintenance_when_brackets_unavailable(monkeypatch):
    client_double = install_client(monkeypatch, wallet=10.0, available=10.0)

    async def fail_brackets(symbol):
        raise RuntimeError("signed read failed")

    client_double.get_leverage_brackets = fail_brackets
    monkeypatch.setattr(prediction_module, "prediction", fake_no_data_prediction())
    client = make_client()
    body = client.get("/api/trading/binance/margin-calculator?symbol=BTCUSDT").json()

    assert body["market"]["maintenance_margin_estimated"] is True


def test_margin_calculator_includes_simulation_when_a_live_decision_exists(monkeypatch):
    install_client(monkeypatch, wallet=10.0, available=10.0)

    async def fake_pred(symbol, interval="5m", timeframe=None, limit=220, current_user=None):
        decision_engine = {
            "active_model": {"model_type": "xgboost"},
            "model_votes": [{"name": "Champion ML", "direction": "LONG", "available": True}],
            "strategy_used": "champion",
            "required_confidence": 70.0,
        }
        return {
            "direction": "LONG", "confidence": 85.0, "target": 64500.0, "stop": 63200.0,
            "decision_engine": decision_engine,
            "prediction": {"direction": "LONG", "confidence": 85.0, "decision_engine": decision_engine,
                           "computed_at": 1750000000000},
        }

    monkeypatch.setattr(prediction_module, "prediction", fake_pred)
    modes.set_mode(modes.MODE_LIVE)
    modes.unlock_live()
    monkeypatch.setattr(settings, "binance_live_enabled", True)

    client = make_client()
    body = client.get("/api/trading/binance/margin-calculator?symbol=BTCUSDT").json()

    assert body["simulation"] is not None
    sim = body["simulation"]
    for field in ("entry_price", "take_profit", "stop_loss", "fees", "liquidation_price",
                  "required_margin", "confidence", "optimal_quantity", "optimal_leverage"):
        assert field in sim
    assert sim["confidence"] == 85.0

    checklist = body["status_checklist"]
    for field in ("model_approved", "risk_gate_approved", "margin_approved", "quantity_valid",
                  "exchange_filters_passed", "order_ready", "overall"):
        assert field in checklist


def test_margin_calculator_recommends_deposit_when_current_setting_fails(monkeypatch):
    install_client(monkeypatch, wallet=10.0, available=10.0)
    monkeypatch.setattr(settings, "binance_default_leverage", 1.0)  # $80 setting needs $80 margin at 1x
    monkeypatch.setattr(prediction_module, "prediction", fake_no_data_prediction())
    client = make_client()
    body = client.get("/api/trading/binance/margin-calculator?symbol=BTCUSDT").json()

    types = [r["type"] for r in body["recommendations"]]
    assert "deposit_funds" in types
    deposit_rec = next(r for r in body["recommendations"] if r["type"] == "deposit_funds")
    assert deposit_rec["impact_usdt"] == body["breakdown_at_current_setting"]["missing"]


def test_margin_calculator_recommends_leverage_increase_when_it_would_help(monkeypatch):
    install_client(monkeypatch, wallet=100.0, available=100.0)
    monkeypatch.setattr(settings, "binance_default_leverage", 1.0)
    monkeypatch.setattr(settings, "binance_max_leverage", 5.0)
    monkeypatch.setattr(settings, "binance_max_notional_per_trade", 80.0)
    monkeypatch.setattr(prediction_module, "prediction", fake_no_data_prediction())
    client = make_client()
    body = client.get("/api/trading/binance/margin-calculator?symbol=BTCUSDT").json()

    types = [r["type"] for r in body["recommendations"]]
    assert "increase_leverage" in types


def test_margin_calculator_never_leaks_secrets(monkeypatch):
    install_client(monkeypatch, wallet=10.0, available=10.0)
    monkeypatch.setattr(prediction_module, "prediction", fake_no_data_prediction())
    client = make_client()
    r = client.get("/api/trading/binance/margin-calculator?symbol=BTCUSDT")
    assert FAKE_KEY not in r.text
    assert FAKE_SECRET not in r.text


# ------------------------------------------------------------ recommendations

def test_symbol_recommendations_ranks_by_lowest_minimum_margin(monkeypatch):
    class VaryingClient(MarginMockClient):
        async def get_exchange_filters(self, symbol):
            f = await super().get_exchange_filters(symbol)
            f["min_notional"] = 5.0 if symbol == "XRPUSDT" else 100.0
            return f

    client = VaryingClient(wallet=10.0, available=10.0)
    monkeypatch.setattr(portfolio_module, "_read_client", client)
    monkeypatch.setattr(settings, "binance_allowed_symbols", ["BTCUSDT", "ETHUSDT", "XRPUSDT"])

    api_client = make_client()
    body = api_client.get("/api/trading/binance/symbol-recommendations").json()

    assert body["available"] is True
    assert len(body["candidates"]) == 5  # all 5 candidates evaluated, allowed or not
    assert body["candidates"][0]["symbol"] == "XRPUSDT"  # lowest min_notional
    assert body["best_symbol"] == "XRPUSDT"


def test_symbol_recommendations_never_marks_disallowed_symbol_as_best(monkeypatch):
    client = MarginMockClient(wallet=10.0, available=10.0, min_notional=5.0)
    monkeypatch.setattr(portfolio_module, "_read_client", client)
    monkeypatch.setattr(settings, "binance_allowed_symbols", ["BTCUSDT"])  # SOL/BNB/XRP/ETH not allowed

    api_client = make_client()
    body = api_client.get("/api/trading/binance/symbol-recommendations").json()

    assert body["best_symbol"] == "BTCUSDT"
    non_allowed = [c for c in body["candidates"] if c["symbol"] != "BTCUSDT"]
    assert all(c["allowed"] is False for c in non_allowed)
