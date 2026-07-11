"""Phase 23: paper bot trades and Binance real bot trades live in different
tables, are served by different endpoints, and never leak into each other."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.bot_trades as bot_trades_module
from app.db.session import SessionLocal
from app.db.models import BinanceBotTrade, Trade, TradingControl
from app.exchanges.binance_models import BinanceOrder
from app.trading import modes
from app.trading.execution_router import BinanceExecutionProvider


@pytest.fixture(autouse=True)
def clean_tables():
    db = SessionLocal()
    try:
        db.query(BinanceBotTrade).delete()
        db.query(TradingControl).delete()
        db.commit()
    finally:
        db.close()
    yield
    modes.set_mode(modes.MODE_PAPER)


def make_client():
    app = FastAPI()
    app.include_router(bot_trades_module.router)
    return TestClient(app)


def add_paper_trade():
    db = SessionLocal()
    try:
        db.add(Trade(symbol="BTCUSDT", side="LONG", entry=50000.0, qty=0.02, status="OPEN",
                     pnl=0.0, decision_mode="champion_ml", strategy_used="trend", confidence=71.0))
        db.commit()
    finally:
        db.close()


def add_binance_bot_trade():
    provider = BinanceExecutionProvider(testnet=True)
    order = BinanceOrder(
        order_id=9001, client_order_id="qx-live-1", symbol="ETHUSDT", side="SELL",
        position_side="BOTH", type="MARKET", status="FILLED", price=0.0, stop_price=0.0,
        quantity=0.05, executed_qty=0.05, avg_price=3000.0, reduce_only=False, close_position=False,
    )
    provider._record_bot_trade(
        action="open", order=order, side="SHORT", notional=20.0, leverage=1.0,
        gate_checks=[{"check": "kill_switch", "passed": True}],
        confidence=72.0, strategy="momentum", model="lightgbm", reason="test intent",
    )


def test_paper_and_binance_bot_trades_never_mix():
    add_paper_trade()
    add_binance_bot_trade()
    client = make_client()

    paper = client.get("/api/bot/trades/paper").json()
    binance = client.get("/api/bot/trades/binance").json()

    assert paper["mode"] == "PAPER"
    assert [t["symbol"] for t in paper["trades"]] == ["BTCUSDT"]
    assert all("order_id" not in t for t in paper["trades"])
    assert paper["trades"][0]["label"] == "BOT_TRADE"
    assert paper["trades"][0]["strategy_used"] == "trend"

    assert binance["mode"] == "BINANCE_LIVE"
    assert [t["symbol"] for t in binance["trades"]] == ["ETHUSDT"]
    assert binance["trades"][0]["order_id"] == 9001
    assert binance["trades"][0]["label"] == "BOT_TRADE"
    assert binance["trades"][0]["risk_gate"][0]["check"] == "kill_switch"
    # the ETHUSDT real trade never shows up in the paper journal and the
    # BTCUSDT paper trade never shows up in the real journal
    assert "ETHUSDT" not in [t["symbol"] for t in paper["trades"]]
    assert "BTCUSDT" not in [t["symbol"] for t in binance["trades"]]


def test_manual_paper_trade_labelled_manual():
    db = SessionLocal()
    try:
        db.add(Trade(symbol="BTCUSDT", side="LONG", entry=50000.0, qty=0.02, status="OPEN",
                     pnl=0.0, decision_mode="manual"))
        db.commit()
    finally:
        db.close()

    trades = make_client().get("/api/bot/trades/paper").json()["trades"]
    assert trades[0]["label"] == "MANUAL"


def test_bot_trade_journal_records_decision_provenance():
    add_binance_bot_trade()
    row = make_client().get("/api/bot/trades/binance").json()["trades"][0]
    assert row["confidence"] == 72.0
    assert row["strategy"] == "momentum"
    assert row["model"] == "lightgbm"
    assert row["notional"] == 20.0
    assert row["client_order_id"] == "qx-live-1"
