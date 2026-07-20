import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.intelligence import hyperliquid_trades as hl


def test_normalize_maps_side_letters_to_buy_sell_and_computes_notional():
    raw = {"coin": "BTC", "side": "B", "px": "60000.5", "sz": "2.0", "time": 1700000000000, "tid": 1, "hash": "0xabc"}
    out = hl._normalize(raw)
    assert out["side"] == "BUY"
    assert out["notional"] == pytest.approx(120001.0)
    assert out["coin"] == "BTC"

    raw_sell = {**raw, "side": "A"}
    assert hl._normalize(raw_sell)["side"] == "SELL"


def test_normalize_returns_none_for_malformed_trade():
    assert hl._normalize({"coin": "BTC", "px": "not-a-number", "sz": "1", "side": "B", "time": 1}) is None
    assert hl._normalize({"coin": "BTC"}) is None


def test_fetch_filters_by_min_notional_and_sorts_newest_first():
    trades = [
        {"coin": "BTC", "side": "BUY", "price": 100.0, "size": 0.1, "notional": 10.0, "time": 3, "trade_id": 1, "hash": "a"},
        {"coin": "BTC", "side": "SELL", "price": 100.0, "size": 1000.0, "notional": 100_000.0, "time": 1, "trade_id": 2, "hash": "b"},
        {"coin": "ETH", "side": "BUY", "price": 100.0, "size": 900.0, "notional": 90_000.0, "time": 2, "trade_id": 3, "hash": "c"},
    ]
    with patch.object(hl, "_listen", AsyncMock(return_value=trades)):
        result = asyncio.run(hl.fetch(min_notional=50_000.0))

    assert result["data_source"] == "hyperliquid_ws"
    assert [t["trade_id"] for t in result["trades"]] == [3, 2]  # newest first, small trade excluded


def test_fetch_reports_unavailable_instead_of_fabricating_trades_on_failure():
    with patch.object(hl, "_listen", AsyncMock(side_effect=ConnectionError("handshake failed"))):
        result = asyncio.run(hl.fetch())

    assert result["data_source"] == "unavailable"
    assert result["trades"] == []
    assert "handshake failed" in result["error"]


class _FakeWebSocket:
    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def recv(self):
        if self._incoming:
            return self._incoming.pop(0)
        await asyncio.sleep(10)  # mimics an idle socket - the caller's own timeout wins

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_listen_subscribes_per_coin_and_parses_only_trades_channel_messages():
    incoming = [
        json.dumps({"channel": "subscriptionResponse", "data": {"type": "trades", "coin": "BTC"}}),
        json.dumps({"channel": "trades", "data": [
            {"coin": "BTC", "side": "B", "px": "60000", "sz": "1.5", "time": 1700000000000, "tid": 1, "hash": "0x1"},
        ]}),
    ]
    fake_ws = _FakeWebSocket(incoming)

    with patch("app.intelligence.hyperliquid_trades.websockets.connect", return_value=fake_ws):
        trades = asyncio.run(hl._listen(("BTC", "ETH"), listen_seconds=0.2))

    assert [s["subscription"]["coin"] for s in fake_ws.sent] == ["BTC", "ETH"]
    assert len(trades) == 1
    assert trades[0]["side"] == "BUY"
    assert trades[0]["notional"] == 90000.0
