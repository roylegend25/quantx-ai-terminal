"""In-process execution metrics tracker plus matching Prometheus series.

A bounded in-memory history is enough here - this process is the only
writer and reader, and GET /api/execution/metrics answers "how has recent
paper execution been performing", not "give me a long-term data warehouse".
Prometheus series are still registered so /api/metrics picks them up for
anyone scraping this process (see app/monitoring/metrics.py).
"""
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from prometheus_client import Counter, Histogram

if TYPE_CHECKING:
    from app.execution.execution_engine import ExecutionResult

EXECUTION_ORDERS_TOTAL = Counter(
    "quantx_execution_orders_total",
    "Execution engine orders by terminal status",
    ["status", "order_type"],
)

EXECUTION_LATENCY = Histogram(
    "quantx_execution_latency_seconds",
    "End-to-end execution engine latency",
    ["order_type"],
)

EXECUTION_SLIPPAGE_BPS = Histogram(
    "quantx_execution_slippage_bps",
    "Actual execution slippage in basis points (absolute value)",
    ["side"],
    buckets=(0, 1, 2, 5, 10, 20, 50, 100, 250, 500),
)

EXECUTION_RETRIES_TOTAL = Counter(
    "quantx_execution_retries_total", "Total execution-engine retry attempts across all orders"
)

_MAX_HISTORY = 200
_history: deque = deque(maxlen=_MAX_HISTORY)


def record(result: "ExecutionResult") -> None:
    _history.append({**asdict(result), "recorded_at": datetime.now(timezone.utc).isoformat()})

    EXECUTION_ORDERS_TOTAL.labels(status=result.status, order_type=result.order_type).inc()
    EXECUTION_LATENCY.labels(order_type=result.order_type).observe(result.latency_ms / 1000)
    if result.actual_slippage_bps is not None:
        EXECUTION_SLIPPAGE_BPS.labels(side=result.side).observe(abs(result.actual_slippage_bps))
    if result.retries:
        EXECUTION_RETRIES_TOTAL.inc(result.retries)


def summary() -> dict:
    if not _history:
        return {
            "total_orders": 0,
            "successful_orders": 0,
            "rejected_orders": 0,
            "partial_fills": 0,
            "partial_fill_rate": 0.0,
            "avg_latency_ms": None,
            "avg_slippage_bps": None,
            "avg_expected_slippage_bps": None,
            "total_retries": 0,
            "quality_breakdown": {},
            "last_execution": None,
        }

    history = list(_history)
    total = len(history)
    successful = [h for h in history if h["status"] in ("FILLED", "PARTIAL")]
    rejected = [h for h in history if h["status"] in ("REJECTED", "CANCELLED", "ERROR")]
    partials = [h for h in history if h["status"] == "PARTIAL"]
    latencies = [h["latency_ms"] for h in history if h.get("latency_ms") is not None]
    actual_slip = [abs(h["actual_slippage_bps"]) for h in history if h.get("actual_slippage_bps") is not None]
    expected_slip = [h["expected_slippage_bps"] for h in history if h.get("expected_slippage_bps") is not None]
    retries = sum(h.get("retries") or 0 for h in history)

    quality_breakdown: dict[str, int] = {}
    for h in history:
        q = h.get("execution_quality")
        if q:
            quality_breakdown[q] = quality_breakdown.get(q, 0) + 1

    return {
        "total_orders": total,
        "successful_orders": len(successful),
        "rejected_orders": len(rejected),
        "partial_fills": len(partials),
        "partial_fill_rate": round(len(partials) / total * 100, 2) if total else 0.0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "avg_slippage_bps": round(sum(actual_slip) / len(actual_slip), 4) if actual_slip else None,
        "avg_expected_slippage_bps": round(sum(expected_slip) / len(expected_slip), 4) if expected_slip else None,
        "total_retries": retries,
        "quality_breakdown": quality_breakdown,
        "last_execution": history[-1],
    }


def recent(limit: int = 20) -> list[dict]:
    return list(_history)[-limit:][::-1]
