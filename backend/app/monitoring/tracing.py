"""Lightweight in-process tracing.

Not a full OpenTelemetry pipeline (no collector/exporter in this stack) -
just enough to correlate the spans within a single request. Every span
shares the current request's request id (see monitoring/logging.py) as its
trace id, so nested spans (e.g. "prediction" containing several
"strategy" spans) can be grouped back together by grepping the JSON logs
for one request_id.
"""
import time
from contextlib import contextmanager
from typing import Iterator

from app.monitoring.logging import get_logger, get_request_id, new_request_id

logger = get_logger("quantx.tracing")


def current_trace_id() -> str:
    """The active request id, or a fresh standalone id if called outside a
    request (e.g. from the background scheduler)."""
    return get_request_id() or new_request_id()


@contextmanager
def span(name: str, **fields) -> Iterator[dict]:
    """Time a unit of work and emit a structured span log line on exit.

    Usage:
        with span("prediction", symbol=symbol) as s:
            ...
            s["confidence"] = pred["confidence"]
    """
    trace_id = current_trace_id()
    start = time.perf_counter()
    payload: dict = {}
    error = None
    try:
        yield payload
    except Exception as exc:
        error = repr(exc)
        raise
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "span",
            extra={
                "event": "span",
                "span": name,
                "trace_id": trace_id,
                "latency_ms": duration_ms,
                "error": error,
                **payload,
            },
        )
