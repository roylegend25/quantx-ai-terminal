"""Phase 20 learning loop: prediction-outcome resolution against recorded
data, aggregation, calibration, and the advisory recommendation engine."""

from datetime import datetime, timedelta, timezone

from app.data_sources import downloader
from app.db.models import DataQualityReport, LearningEvaluation, PredictionFeature
from app.db.session import SessionLocal
from app.learning import evaluator, recommender

TF_MS = 5 * 60 * 1000
START = 1_720_000_000_000


def _seed_candles(db, closes, symbol="BTCUSDT", timeframe="5m"):
    rows = []
    for i, close in enumerate(closes):
        rows.append(
            {
                "time": START + i * TF_MS,
                "open": close,
                "high": close * 1.001,
                "low": close * 0.999,
                "close": close,
                "volume": 10.0,
            }
        )
    downloader.store_candles(db, symbol, timeframe, "binance_futures", rows, quality=99.0)


def _prediction(db, i, direction, confidence, entry, target=None, timeframe="5m", outcome=None, exit_price=None):
    row = PredictionFeature(
        timestamp=datetime.fromtimestamp((START + i * TF_MS) / 1000, tz=timezone.utc),
        symbol="BTCUSDT",
        timeframe=timeframe,
        direction=direction,
        confidence=confidence,
        regime="TRENDING_UP",
        entry_price=entry,
        target=target,
        outcome=outcome,
        exit_price=exit_price,
    )
    db.add(row)
    db.commit()
    return row


def test_evaluate_resolves_against_market_candles():
    db = SessionLocal()
    try:
        db.query(PredictionFeature).delete()
        db.query(LearningEvaluation).delete()
        db.commit()

        closes = [100.0 + i for i in range(20)]  # steadily rising market
        _seed_candles(db, closes)

        _prediction(db, 2, "LONG", 80.0, closes[2], target=closes[2] * 1.01)   # correct (price rose)
        _prediction(db, 5, "SHORT", 70.0, closes[5], target=closes[5] * 0.99)  # incorrect
        _prediction(db, 8, "LONG", 90.0, closes[8], outcome="WIN", exit_price=closes[10])  # paper trade
        _prediction(db, 11, "NO_TRADE", 0.0, closes[11])  # excluded from accuracy

        perf = evaluator.evaluate(symbol="BTCUSDT", db=db)
        assert perf["predictions_considered"] == 4
        assert perf["predictions_resolved"] == 3
        assert perf["direction_hit_rate_pct"] == round(2 / 3 * 100, 2)
        assert perf["by_direction"]["LONG"]["hit_rate_pct"] == 100.0
        assert perf["by_direction"]["SHORT"]["hit_rate_pct"] == 0.0
        sources = perf["details"]["resolution_sources"]
        assert "paper_trade" in sources and "market_candles" in sources

        # snapshot persisted and retrievable
        assert evaluator.latest_performance(db=db)["predictions_resolved"] == 3
    finally:
        db.close()


def test_evaluate_leaves_unresolvable_predictions_pending():
    db = SessionLocal()
    try:
        db.query(PredictionFeature).delete()
        db.query(LearningEvaluation).delete()
        db.commit()

        # prediction on a symbol/timeframe with no stored candles and no
        # successor prediction: must stay unresolved, never invented
        _prediction(db, 0, "LONG", 75.0, 500.0, timeframe="2h")
        perf = evaluator.evaluate(symbol="BTCUSDT", db=db)
        assert perf["predictions_considered"] == 1
        assert perf["predictions_resolved"] == 0
        assert perf["direction_hit_rate_pct"] is None
    finally:
        db.close()


def test_strategy_weights_exposes_live_and_candidate():
    db = SessionLocal()
    try:
        from app.strategy.performance_repository import repository

        repository.seed_defaults(db)
        result = recommender.strategy_weights(db=db)
        assert set(result["live_weights"]) == set(result["candidate_weights"])
        assert abs(sum(result["candidate_weights"].values()) - 1.0) < 1e-3
        assert "note" in result
    finally:
        db.close()


def test_recommendations_flag_bad_data_quality():
    db = SessionLocal()
    try:
        db.add(
            DataQualityReport(
                symbol="ETHUSDT",
                timeframe="5m",
                timestamp=START,
                rows=100,
                missing_candles=50,
                outliers=5,
                quality_score=35.0,
            )
        )
        db.commit()

        result = recommender.recommendations(db=db)
        assert result["count"] >= 1
        quality_recs = [r for r in result["recommendations"] if r["type"] == "data_quality"]
        assert quality_recs and quality_recs[0]["severity"] == "high"
        assert "ETHUSDT" in quality_recs[0]["reason"]
        # advisory contract
        assert "nothing is retrained or promoted automatically" in result["note"].lower()
    finally:
        db.close()
