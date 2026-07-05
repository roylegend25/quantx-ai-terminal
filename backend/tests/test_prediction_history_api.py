from datetime import datetime, timedelta, timezone

from app.api.prediction import prediction_history, serialize_history_rows
from app.db.models import PredictionFeature
from app.db.session import SessionLocal


def _seed(db, *, minutes_ago: int, symbol="BTCUSDT", timeframe="1h", direction="LONG",
          confidence=70.0, entry=100.0, target=110.0, stop=95.0, exit_price=None,
          outcome=None, weights=None):
    row = PredictionFeature(
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        confidence=confidence,
        regime="bullish_trend",
        adaptive_strategy_weights=weights or {"trend": 0.4, "momentum": 0.3, "mean_reversion": 0.2, "breakout": 0.1},
        entry_price=entry,
        target=target,
        stop=stop,
        exit_price=exit_price,
        outcome=outcome,
    )
    db.add(row)
    db.commit()
    return row


def _clean(db):
    db.query(PredictionFeature).delete()
    db.commit()


def test_history_resolves_actual_price_from_next_prediction():
    db = SessionLocal()
    try:
        _clean(db)
        _seed(db, minutes_ago=120, direction="LONG", entry=100.0, target=110.0)
        _seed(db, minutes_ago=60, direction="SHORT", entry=108.0, target=101.0)
        _seed(db, minutes_ago=1, direction="LONG", entry=104.0, target=112.0)

        res = prediction_history(symbol="BTCUSDT", timeframe="1h", limit=100)
    finally:
        _clean(db)
        db.close()

    points = res["history"]
    assert res["count"] == 3
    # ascending order
    assert points[0]["timestamp"] < points[1]["timestamp"] < points[2]["timestamp"]

    # first LONG resolved against second row's entry (108 > 100 -> CORRECT)
    assert points[0]["actual_price"] == 108.0
    assert points[0]["outcome"] == "CORRECT"
    assert points[0]["predicted_price"] == 110.0
    assert points[0]["strategy"] == "trend"

    # second SHORT resolved against third row's entry (104 < 108 -> CORRECT)
    assert points[1]["outcome"] == "CORRECT"

    # newest row has nothing to resolve against yet
    assert points[2]["actual_price"] is None
    assert points[2]["outcome"] == "PENDING"

    assert res["summary"]["resolved"] == 2
    assert res["summary"]["direction_hit_rate_pct"] == 100.0


def test_history_prefers_trade_exit_price_and_recorded_outcome():
    db = SessionLocal()
    try:
        _clean(db)
        _seed(db, minutes_ago=60, direction="LONG", entry=100.0, target=110.0,
              exit_price=109.0, outcome="WIN")
        _seed(db, minutes_ago=1, direction="NO_TRADE", entry=109.5, target=None, stop=None)

        res = prediction_history(symbol="BTCUSDT", timeframe="1h", limit=100)
    finally:
        _clean(db)
        db.close()

    points = res["history"]
    assert points[0]["actual_price"] == 109.0  # trade exit, not next entry
    assert points[0]["outcome"] == "WIN"
    # NO_TRADE rows fall back to entry as their plotted prediction
    assert points[1]["predicted_price"] == 109.5
    assert points[1]["outcome"] == "NO_TRADE"


def test_history_filters_by_symbol_and_timeframe():
    db = SessionLocal()
    try:
        _clean(db)
        _seed(db, minutes_ago=10, symbol="BTCUSDT", timeframe="1h")
        _seed(db, minutes_ago=9, symbol="BTCUSDT", timeframe="5m")
        _seed(db, minutes_ago=8, symbol="ETHUSDT", timeframe="1h")

        all_btc = prediction_history(symbol="btcusdt", limit=100)
        only_1h = prediction_history(symbol="BTCUSDT", timeframe="1h", limit=100)
    finally:
        _clean(db)
        db.close()

    assert all_btc["count"] == 2
    assert only_1h["count"] == 1
    assert only_1h["history"][0]["timeframe"] == "1h"


def test_serialize_handles_missing_prices():
    row = PredictionFeature(
        id=1,
        timestamp=datetime.now(timezone.utc),
        symbol="BTCUSDT",
        timeframe="1h",
        direction="LONG",
        confidence=55.0,
        entry_price=None,
        target=None,
        stop=None,
    )
    points = serialize_history_rows([row])
    assert points[0]["predicted_price"] is None
    assert points[0]["outcome"] == "PENDING"
    assert points[0]["accuracy_pct"] is None
