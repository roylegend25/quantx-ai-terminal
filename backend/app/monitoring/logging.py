"""Structured JSON logging for the QuantX backend.

Every log line is a single JSON object on stdout so it can be shipped
straight into any log aggregator (Loki, CloudWatch, ELK, ...) without a
parsing step. Request-scoped fields (request id, endpoint, latency, ...)
are attached via ``extra=`` and picked up by :class:`JsonFormatter`.
"""
import asyncio
import contextvars
import itertools
import json
import logging
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

# Fields callers may attach via `extra={...}` and that we know how to surface.
_STRUCTURED_FIELDS = (
    "endpoint",
    "method",
    "status_code",
    "prediction_id",
    "strategy",
    "confidence",
    "latency_ms",
    "error",
    "event",
    "span",
    "trace_id",
    "symbol",
    "category",
    "trade_id",
    "side",
    "entry",
    "exit_price",
    "pnl",
    "reason",
    "price",
    "direction",
)


def new_request_id() -> str:
    return uuid.uuid4().hex


def get_request_id() -> str | None:
    return _request_id_var.get()


def set_request_id(value: str) -> contextvars.Token:
    return _request_id_var.set(value)


def reset_request_id(token: contextvars.Token) -> None:
    _request_id_var.reset(token)


def _build_payload(record: logging.LogRecord) -> dict:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
        "request_id": get_request_id(),
    }

    for field in _STRUCTURED_FIELDS:
        value = getattr(record, field, None)
        if value is not None:
            payload[field] = value

    if record.exc_info and "error" not in payload:
        payload["error"] = logging.Formatter().formatException(record.exc_info)

    payload.setdefault("category", "system")
    return payload


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_build_payload(record), default=str)


# In-memory ring buffer backing the read-only /api/logs endpoints. This is
# intentionally process-local (no DB/file persistence): it just needs to
# hold "recent" events for the Logs page, not a durable audit trail.
_LOG_BUFFER_MAXLEN = 1000
_log_buffer: "deque[dict]" = deque(maxlen=_LOG_BUFFER_MAXLEN)
_log_id_counter = itertools.count(1)
_log_subscribers: "set[asyncio.Queue]" = set()


class RingBufferHandler(logging.Handler):
    """Feeds every structured log event into the in-memory ring buffer and
    fans it out to any live /api/logs/stream subscribers."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = _build_payload(record)
        except Exception:
            return
        payload["id"] = next(_log_id_counter)
        _log_buffer.append(payload)
        for queue in list(_log_subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass


_ring_handler = RingBufferHandler()


def get_logger(name: str = "quantx") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.addHandler(_ring_handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(logger: logging.Logger, message: str = "event", level: int = logging.INFO, **fields) -> None:
    """Emit one structured log line. Unknown kwargs are dropped silently by
    the formatter rather than raising, so callers can pass whatever context
    they have without worrying about the fixed field list."""
    logger.log(level, message, extra=fields)


def get_recent_logs(
    limit: int = 200,
    since_id: int | None = None,
    level: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """Read-only snapshot of the ring buffer, newest-last (same order the
    events were emitted in) so the frontend can just append them."""
    items = list(_log_buffer)
    if since_id is not None:
        items = [e for e in items if e["id"] > since_id]
    if level:
        level = level.upper()
        items = [e for e in items if e.get("level") == level]
    if category:
        category = category.lower()
        items = [e for e in items if e.get("category") == category]
    return items[-limit:]


def subscribe_logs() -> "asyncio.Queue":
    queue: "asyncio.Queue" = asyncio.Queue(maxsize=500)
    _log_subscribers.add(queue)
    return queue


def unsubscribe_logs(queue: "asyncio.Queue") -> None:
    _log_subscribers.discard(queue)


_request_logger = get_logger("quantx.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs one structured JSON line per HTTP request: request id, endpoint,
    method, status, latency and error (if any)."""

    async def dispatch(self, request: Request, call_next):
        request_id = new_request_id()
        token = set_request_id(request_id)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            log_event(
                _request_logger,
                message="request_failed",
                level=logging.ERROR,
                category="api",
                endpoint=request.url.path,
                method=request.method,
                status_code=500,
                latency_ms=latency_ms,
                error=repr(exc),
            )
            reset_request_id(token)
            raise

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        log_event(
            _request_logger,
            message="request_completed",
            category="api",
            endpoint=request.url.path,
            method=request.method,
            status_code=response.status_code,
            latency_ms=latency_ms,
            error=None,
        )
        reset_request_id(token)
        return response
