"""Structured JSON logging for the QuantX backend.

Every log line is a single JSON object on stdout so it can be shipped
straight into any log aggregator (Loki, CloudWatch, ELK, ...) without a
parsing step. Request-scoped fields (request id, endpoint, latency, ...)
are attached via ``extra=`` and picked up by :class:`JsonFormatter`.
"""
import contextvars
import json
import logging
import sys
import time
import uuid
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
)


def new_request_id() -> str:
    return uuid.uuid4().hex


def get_request_id() -> str | None:
    return _request_id_var.get()


def set_request_id(value: str) -> contextvars.Token:
    return _request_id_var.set(value)


def reset_request_id(token: contextvars.Token) -> None:
    _request_id_var.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
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
            payload["error"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def get_logger(name: str = "quantx") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(logger: logging.Logger, message: str = "event", level: int = logging.INFO, **fields) -> None:
    """Emit one structured log line. Unknown kwargs are dropped silently by
    the formatter rather than raising, so callers can pass whatever context
    they have without worrying about the fixed field list."""
    logger.log(level, message, extra=fields)


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
            endpoint=request.url.path,
            method=request.method,
            status_code=response.status_code,
            latency_ms=latency_ms,
            error=None,
        )
        reset_request_id(token)
        return response
