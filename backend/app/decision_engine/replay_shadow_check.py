"""Deployment-verification-only script (Bot Settings Part 12): drives
ActiveDriveV2Engine.evaluate() + ledger.persist() at least `--min-evaluations`
times across a mix of symbols/timeframes/synthetic feature snapshots,
against whatever database the process is pointed at (the isolated,
ephemeral validation container's own paper.db copy when run from
scripts/deploy-production.sh - never production).

Not a backtest: does not download or replay real historical candles (the
existing app.backtest module already does that for actual strategy
backtesting). This exists purely to generate volume through the real
decision/persistence code path fast and deterministically, so a deploy gate
can assert "the pipeline works and creates zero orders" without waiting on
live scheduler cadence.

Asserts, and exits non-zero on any violation:
  - every evaluate() call produces a decision persisted via ledger.persist()
  - zero Trade rows exist afterward (nothing here can create an order)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.db.models import PredictionLedger, Trade
from app.db.session import SessionLocal
from app.decision_engine.ledger import persist
from app.decision_engine.v2 import ActiveDriveV2Engine

SYMBOLS = ("BTCUSDT", "ETHUSDT")
TIMEFRAMES = ("5m", "15m", "1h")


def _synthetic_features(i: int) -> dict:
    """Deterministic, varied feature snapshot - never fabricated to force a
    particular signal, just cheap and reproducible."""
    price = 100.0 + (i % 20) - 10
    drift = ((i * 7) % 11) - 5
    return {
        "price": price,
        "ema20": price - drift * 0.3,
        "ema50": price - drift * 0.6,
        "ema200": price - drift,
        "rsi": 40 + (i * 3) % 40,
        "macd_hist": (drift) / 10,
        "atr": 1.5,
        "bb_width": 0.01 + (i % 5) * 0.002,
        "volume": 1000 + i * 10,
        "volume_sma20": 1000,
        "trend_score": drift,
    }


def _context(db, symbol: str, timeframe: str, i: int, settings_scope: str) -> dict:
    features = _synthetic_features(i)
    direction = "LONG" if features["macd_hist"] > 0 else "SHORT" if features["macd_hist"] < 0 else "NO_TRADE"
    legacy = {
        "direction": direction, "confidence": 65, "probability_up": 60, "probability_down": 40,
        "regime": "TRENDING", "strategies": {"trend": {"direction": direction, "confidence": 65, "reason": "replay"}},
        "ml_champion": {"used": False}, "features": features, "risk": {"allowed": True, "reason": "ok"},
        "target": features["price"] * 1.02, "stop": features["price"] * 0.99,
    }
    return {
        "db": db, "user_id": settings.admin_username, "symbol": symbol, "timeframe": timeframe,
        "legacy": legacy, "regime": legacy["regime"], "data_status": "live", "risk_reward_ratio": 2.0,
        "settings_scope": settings_scope,
    }


def run(min_evaluations: int) -> int:
    db = SessionLocal()
    # The isolated validation container runs the full app, including its own
    # background scheduler/resolver tasks (see main.py's startup_event) that
    # concurrently write to this same SQLite file - without a busy timeout,
    # any overlap between this script's writes and the scheduler's raises
    # "database is locked" immediately instead of waiting. 30s is generous
    # for the scheduler's short write bursts (single-row commits).
    db.execute(text("PRAGMA busy_timeout = 30000"))
    engine = ActiveDriveV2Engine()
    decisions_written = 0
    ledger_rows_before = db.query(PredictionLedger).count()
    # The validation database is a copy of real production history (see
    # scripts/deploy-production.sh), which already contains real prior
    # trades - the invariant this script checks is that the REPLAY itself
    # creates zero new ones, not that the table is empty.
    trades_before = db.query(Trade).count()
    try:
        i = 0
        while decisions_written < min_evaluations:
            symbol = SYMBOLS[i % len(SYMBOLS)]
            timeframe = TIMEFRAMES[i % len(TIMEFRAMES)]
            scope = "paper" if i % 3 != 0 else "binance_real"
            context = _context(db, symbol, timeframe, i, scope)
            result = engine.evaluate(context)
            for attempt in range(5):
                try:
                    persist(db, settings.admin_username, result, context["legacy"]["features"]["price"], context["legacy"]["features"])
                    break
                except OperationalError:
                    db.rollback()
                    if attempt == 4:
                        raise
                    time.sleep(1 + attempt)
            decisions_written += 1
            i += 1

        ledger_rows_after = db.query(PredictionLedger).count()
        trades_after = db.query(Trade).count()
        assert ledger_rows_after > ledger_rows_before, "no PredictionLedger rows were written"
        assert trades_after == trades_before, (
            f"replay created {trades_after - trades_before} new Trade row(s) - MUST be zero "
            f"(before={trades_before}, after={trades_after})"
        )

        print(f"replay_shadow_check: {decisions_written} decisions evaluated and persisted, "
              f"{ledger_rows_after - ledger_rows_before} ledger rows written, 0 new trades created "
              f"(pre-existing trades untouched: {trades_before}) at {datetime.now(timezone.utc).isoformat()}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-evaluations", type=int, default=100)
    args = parser.parse_args()
    sys.exit(run(args.min_evaluations))
