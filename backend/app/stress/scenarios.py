"""Registry of stress-test scenarios exercised by app.stress.simulator.

Each scenario documents one bad market/data/infrastructure condition QuantX
must survive without executing an unsafe trade or leaving an open paper
position unprotected. This module only describes *what* is being tested -
app/stress/simulator.py owns the actual fault injection and assertions.

Every scenario is read-only against the paper-trading database and never
opens/closes a trade or touches live trading.
"""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    category: str
    description: str


SCENARIOS: list[Scenario] = [
    Scenario(
        "binance_api_timeout",
        "Binance API Timeout",
        "market_data",
        "The exchange klines request hangs/times out mid prediction cycle.",
    ),
    Scenario(
        "missing_candles",
        "Missing Candles",
        "market_data",
        "Only a handful of candles are available (cold start / gap in history).",
    ),
    Scenario(
        "stale_price_data",
        "Stale Price Data",
        "market_data",
        "The last candle is frozen (flat) and old relative to wall-clock time.",
    ),
    Scenario(
        "extreme_spread",
        "Extreme Spread",
        "market_data",
        "The order book bid/ask spread blows out to several percent.",
    ),
    Scenario(
        "flash_crash",
        "Flash Crash -5%",
        "market_shock",
        "Price instantly drops 5% on the latest candle.",
    ),
    Scenario(
        "flash_pump",
        "Flash Pump +5%",
        "market_shock",
        "Price instantly spikes 5% on the latest candle.",
    ),
    Scenario(
        "redis_unavailable",
        "Redis Unavailable",
        "infrastructure",
        "The Redis cache cannot be reached.",
    ),
    Scenario(
        "database_unavailable",
        "Database Unavailable",
        "infrastructure",
        "The primary database connection is down.",
    ),
    Scenario(
        "scheduler_delay",
        "Scheduler Delay",
        "orchestration",
        "The trading scheduler cycle is delayed far beyond its configured interval.",
    ),
    Scenario(
        "duplicate_prediction_cycle",
        "Duplicate Prediction Cycle",
        "orchestration",
        "Two prediction/trading cycles fire concurrently (double-start, multi-worker).",
    ),
    Scenario(
        "position_manager_delayed",
        "Position Manager Delayed",
        "orchestration",
        "The position manager's polling loop falls behind.",
    ),
    Scenario(
        "high_latency",
        "High Latency",
        "performance",
        "End-to-end prediction latency spikes well above normal.",
    ),
    Scenario(
        "zero_volume_candle",
        "Zero Volume Candle",
        "market_data",
        "The latest candles report zero trading volume (halted/illiquid market).",
    ),
    Scenario(
        "bad_orderbook_response",
        "Bad Orderbook Response",
        "market_data",
        "The exchange returns a malformed/empty order book payload.",
    ),
    Scenario(
        "model_unavailable",
        "Model Unavailable",
        "ml",
        "A trained ML model artifact is missing or fails to load.",
    ),
]

_BY_ID = {s.id: s for s in SCENARIOS}


def list_scenarios() -> list[dict]:
    return [asdict(s) for s in SCENARIOS]


def get_scenario(scenario_id: str) -> Scenario | None:
    return _BY_ID.get(scenario_id)
