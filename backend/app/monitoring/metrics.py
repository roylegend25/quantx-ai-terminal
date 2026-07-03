"""Prometheus metrics for the QuantX backend.

Counters/histograms are updated inline where the relevant event happens
(a request completes, a paper trade opens, a strategy runs, ...). Gauges
that represent "current state" (win rate, PnL, open positions) are instead
refreshed from the database right before every /api/metrics scrape via
:func:`refresh_state_gauges`, so they can never drift out of sync between
scrapes.
"""
import time
from collections import deque

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

API_REQUEST_COUNT = Counter(
    "quantx_api_requests_total",
    "Total API requests handled",
    ["method", "endpoint", "status"],
)

API_REQUEST_LATENCY = Histogram(
    "quantx_api_request_latency_seconds",
    "API request latency in seconds",
    ["method", "endpoint"],
)

# Rolling window of recent request latencies (ms), used to answer "what's
# the current API latency" for the System Status page without reaching
# into Histogram internals.
_RECENT_LATENCY_MS: deque[float] = deque(maxlen=200)


def recent_avg_latency_ms() -> float | None:
    if not _RECENT_LATENCY_MS:
        return None
    return round(sum(_RECENT_LATENCY_MS) / len(_RECENT_LATENCY_MS), 2)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path or request.url.path


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Records request count + latency for every HTTP request."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - start
            endpoint = _route_template(request)
            API_REQUEST_LATENCY.labels(method=request.method, endpoint=endpoint).observe(duration)
            API_REQUEST_COUNT.labels(method=request.method, endpoint=endpoint, status="500").inc()
            _RECENT_LATENCY_MS.append(duration * 1000)
            raise

        duration = time.perf_counter() - start
        endpoint = _route_template(request)
        API_REQUEST_LATENCY.labels(method=request.method, endpoint=endpoint).observe(duration)
        API_REQUEST_COUNT.labels(
            method=request.method, endpoint=endpoint, status=str(response.status_code)
        ).inc()
        _RECENT_LATENCY_MS.append(duration * 1000)
        return response


# ---------------------------------------------------------------------------
# Prediction / strategy / scheduler
# ---------------------------------------------------------------------------

PREDICTION_LATENCY = Histogram(
    "quantx_prediction_latency_seconds",
    "End-to-end prediction generation latency",
    ["symbol"],
)

STRATEGY_EXECUTION_TIME = Histogram(
    "quantx_strategy_execution_seconds",
    "Per-strategy evaluation latency",
    ["strategy"],
)

SCHEDULER_CYCLE_LATENCY = Histogram(
    "quantx_scheduler_cycle_seconds",
    "Background trading-engine scheduler cycle latency",
)

# ---------------------------------------------------------------------------
# Paper trading
# ---------------------------------------------------------------------------

PAPER_TRADES_OPENED = Counter(
    "quantx_paper_trades_opened_total",
    "Paper trades opened",
    ["symbol", "side"],
)

PAPER_TRADES_CLOSED = Counter(
    "quantx_paper_trades_closed_total",
    "Paper trades closed",
    ["symbol"],
)

WIN_RATE = Gauge("quantx_win_rate_percent", "Current paper-trading win rate percent")
PNL_TOTAL = Gauge("quantx_pnl_total", "Total realized paper PnL")
ACTIVE_POSITIONS = Gauge("quantx_active_positions", "Number of currently open paper positions")


def refresh_state_gauges(db: Session) -> None:
    from app.db.models import Portfolio, Trade

    portfolio = db.get(Portfolio, 1)
    if portfolio:
        total = portfolio.wins + portfolio.losses
        WIN_RATE.set((portfolio.wins / total) * 100 if total else 0.0)
        PNL_TOTAL.set(portfolio.total_pnl)
    else:
        WIN_RATE.set(0.0)
        PNL_TOTAL.set(0.0)

    open_count = db.query(Trade).filter(Trade.status == "OPEN").count()
    ACTIVE_POSITIONS.set(open_count)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DB_QUERY_DURATION = Histogram(
    "quantx_db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation"],
)

_QUERY_START_KEY = "_quantx_query_start"


def instrument_db_engine(engine: Engine) -> None:
    """Attach before/after cursor-execute listeners so every query issued
    through this engine (regardless of call site) reports its duration -
    no changes needed at any of the existing query call sites."""

    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault(_QUERY_START_KEY, []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        stack = conn.info.get(_QUERY_START_KEY)
        if not stack:
            return
        start = stack.pop()
        operation = statement.strip().split(" ", 1)[0].upper() if statement.strip() else "UNKNOWN"
        DB_QUERY_DURATION.labels(operation=operation).observe(time.perf_counter() - start)


def render_latest() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
