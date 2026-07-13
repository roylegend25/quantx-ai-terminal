"""Global Binance REST governor (root-cause fix for "Rate limited by
Binance" appearing across the app).

Every Binance REST call this app makes - every endpoint, the protection
watchdog, the margin calculator, diagnostics, everything - eventually
calls BinanceFuturesClient._request, which calls this module immediately
before the HTTP round trip (to decide whether the call is even allowed to
happen right now) and immediately after (to learn from the response).
This is the ONE place that knows whether Binance is currently unhappy
with this IP - no individual caller needs its own retry/backoff logic,
and no caller can accidentally bypass it by constructing "just one more"
client, since every client funnels through the same `_request`.

Binance's own docs: a 429 means "back off before your next request";
repeated violations (or hammering right after a 418) is what triggers an
IP ban. This limiter's whole job is to make that impossible to do by
accident - it tracks a cooldown after 429 and a hard ban window after
418, and gates every call by priority so a background diagnostics poll
can never be the thing that keeps a real ban alive.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.exchanges.binance_errors import BinanceRateLimitError

# Priority levels (Phase 29 rate limiting). Lower rank = more important =
# survives a cooldown longer. Writes (close/cancel/emergency-stop/protection
# repair) are always CRITICAL; the client sets sane defaults per call, see
# binance_futures_client.py.
CRITICAL = "CRITICAL"  # close position, cancel order, emergency stop, protection repair/verify
HIGH = "HIGH"          # live positions, open orders, account balance
NORMAL = "NORMAL"      # dashboard portfolio, readiness checklist, risk status
LOW = "LOW"            # diagnostics, historical income, performance history, decorative cards

_RANK = {CRITICAL: 0, HIGH: 1, NORMAL: 2, LOW: 3}

DEFAULT_SOFT_COOLDOWN_SECONDS = 5.0
DEFAULT_HARD_BAN_SECONDS = 120.0
# Root cause of a cooldown that "never clears" (e.g. showing "retry in
# 985s" long after the rate-limit snapshot service was added): HIGH/
# CRITICAL priority calls (positions, orders, balance, cancel, repair) are
# deliberately exempt from the soft-cooldown gate in before_request() - if
# Binance is still genuinely limiting the IP, each one of those keeps
# landing a fresh 429, and the exponential backoff below
# (base * multiplier ** consecutive_429) had no ceiling, so a real streak
# of ~14 consecutive 429s could compound past 900+ seconds before the
# window ever had a chance to actually expire. This cap bounds our OWN
# compounding; an explicit Retry-After Binance sends on any single 429 is
# still honored even past this cap (see after_response).
MAX_SOFT_COOLDOWN_SECONDS = 120.0


class BinanceCooldownActive(BinanceRateLimitError):
    """Raised by the limiter INSTEAD of making a request, whenever this
    priority level must not call Binance right now. Subclasses
    BinanceRateLimitError so every existing `except BinanceRateLimitError`
    / `except BinanceError` call site already treats it exactly like a
    real 429/418 response - callers never need special-casing to get the
    safe behavior."""

    def __init__(self, retry_after_seconds: float, reason: str, banned: bool = False):
        super().__init__(reason, code=None, status=418 if banned else 429)
        self.retry_after_seconds = round(max(retry_after_seconds, 0.0), 1)
        self.banned = banned


@dataclass
class _EndpointStats:
    count: int = 0
    last_called_at: float | None = None


class BinanceRateLimiter:
    """Process-wide singleton - one governor for the whole app, matching
    the single long-lived read-only client already used for account
    reads (app.api.portfolio.get_read_client)."""

    def __init__(self, backoff_multiplier: float = 1.5):
        self.backoff_multiplier = backoff_multiplier
        self._cooldown_until = 0.0
        self._ban_until = 0.0
        self._consecutive_429 = 0
        self._last_429_at: float | None = None
        self._last_418_at: float | None = None
        self._last_success_at: float | None = None
        self._last_retry_after: float | None = None
        self._weight_used_1m: int | None = None
        self._order_count_1m: int | None = None
        self._endpoints: dict[str, _EndpointStats] = {}
        self._window_start = time.time()
        self._window_count = 0

    # ------------------------------------------------------------- gating

    def before_request(self, endpoint: str, priority: str = NORMAL) -> None:
        """Raises BinanceCooldownActive instead of letting the call
        through when this priority isn't allowed right now. CRITICAL is
        only ever withheld during a hard 418 ban (repair/close/cancel
        must still get a chance during an ordinary 429 cooldown - holding
        those back is more dangerous than one more request)."""
        now = time.time()
        if now < self._ban_until and priority != CRITICAL:
            raise BinanceCooldownActive(
                self._ban_until - now, "Binance temporary IP ban / cooldown active", banned=True,
            )
        if now < self._cooldown_until and priority in (LOW, NORMAL):
            raise BinanceCooldownActive(self._cooldown_until - now, "Binance API cooling down after a rate limit")
        self._record_call(endpoint)

    def _record_call(self, endpoint: str) -> None:
        now = time.time()
        if now - self._window_start >= 60:
            self._window_start = now
            self._window_count = 0
        self._window_count += 1
        stat = self._endpoints.setdefault(endpoint, _EndpointStats())
        stat.count += 1
        stat.last_called_at = now

    # ------------------------------------------------------------ learning

    def after_response(self, endpoint: str, status_code: int, headers) -> None:
        """Called with the real HTTP response so the limiter can learn
        Binance's own view of this IP's usage (used-weight/order-count
        headers) and react to 429/418 with a growing cooldown/ban."""
        weight = headers.get("X-MBX-USED-WEIGHT-1M") or headers.get("x-mbx-used-weight-1m")
        if weight is not None:
            try:
                self._weight_used_1m = int(weight)
            except ValueError:
                pass
        order_count = headers.get("X-MBX-ORDER-COUNT-1M") or headers.get("x-mbx-order-count-1m")
        if order_count is not None:
            try:
                self._order_count_1m = int(order_count)
            except ValueError:
                pass

        if status_code == 418:
            retry_after = self._retry_after_seconds(headers, default=DEFAULT_HARD_BAN_SECONDS)
            self._last_418_at = time.time()
            self._ban_until = time.time() + retry_after
            self._last_retry_after = retry_after
            return

        if status_code == 429:
            self._consecutive_429 += 1
            base = self._retry_after_seconds(headers, default=DEFAULT_SOFT_COOLDOWN_SECONDS)
            backoff = base * (self.backoff_multiplier ** (self._consecutive_429 - 1))
            # Cap our OWN compounding growth - never cap below Binance's
            # own explicit Retry-After for THIS response (`base`), only
            # the multiplier's runaway growth across many consecutive
            # violations. See MAX_SOFT_COOLDOWN_SECONDS above.
            backoff = min(backoff, max(base, MAX_SOFT_COOLDOWN_SECONDS))
            self._last_429_at = time.time()
            self._cooldown_until = time.time() + backoff
            self._last_retry_after = backoff
            return

        # Any non-429/418 response is live, current, authoritative proof
        # Binance is fine with this IP against the exact same rate-limit
        # window it enforces - unlike a merely-elapsed timer, this is
        # actual confirmation. A 2xx now clears an active soft cooldown
        # early and resets the consecutive-429 streak (section 3: "when
        # cooldown expires... if successful, clear cooldown, reset
        # repeated-429 counter, mark healthy"). A hard 418 ban is never
        # cleared this way - only CRITICAL calls can even reach Binance
        # during one, and a single such success is deliberately not
        # trusted to end an IP-level ban early.
        if status_code // 100 == 2:
            self._consecutive_429 = 0
            self._cooldown_until = 0.0
            self._last_success_at = time.time()

    def _retry_after_seconds(self, headers, default: float) -> float:
        """Binance's Retry-After is normally a small RFC 7231 delta-
        seconds value, but defends against two malformed shapes: a
        millisecond epoch timestamp and a bare epoch-seconds timestamp -
        either would otherwise be read as a literal seconds-to-wait value
        and produce a cooldown lasting until sometime next century."""
        value = headers.get("Retry-After") or headers.get("retry-after")
        if value is None:
            return default
        try:
            parsed = float(value)
        except ValueError:
            return default
        if parsed <= 120:
            return max(parsed, 1.0)
        now = time.time()
        if parsed > now * 1000 * 0.5:
            # looks like a millisecond epoch timestamp
            return max((parsed / 1000.0) - now, 1.0)
        if parsed > now:
            # looks like a bare epoch-seconds timestamp
            return max(parsed - now, 1.0)
        # An unusually large plain delta-seconds value - respected as-is
        # (this is what lets an explicit, valid, longer Retry-After from
        # Binance survive the soft-cooldown cap above).
        return parsed

    # ------------------------------------------------------------- status

    def status(self) -> dict:
        now = time.time()
        banned = now < self._ban_until
        cooling_down = now < self._cooldown_until
        retry_after = None
        if banned:
            retry_after = round(self._ban_until - now, 1)
        elif cooling_down:
            retry_after = round(self._cooldown_until - now, 1)
        return {
            "rate_limited": banned or cooling_down,
            "banned": banned,
            "retry_after_seconds": retry_after,
            "requests_per_minute": self._window_count,
            "weight_used_1m": self._weight_used_1m,
            "order_count_used_1m": self._order_count_1m,
            "last_429_at": self._last_429_at,
            "last_418_at": self._last_418_at,
            "cooldown_until": self._cooldown_until or None,
            "ban_until": self._ban_until or None,
            "top_endpoints": sorted(
                ({"endpoint": k, "count": v.count} for k, v in self._endpoints.items()),
                key=lambda x: -x["count"],
            )[:10],
            # Debug 2.pdf section 3: additive fields, same units/shape as
            # the ones above (epoch seconds, not ISO strings) so no
            # existing caller needs to change.
            "cooldown_active": cooling_down,
            "ban_active": banned,
            "consecutive_429s": self._consecutive_429,
            "last_success_at": self._last_success_at,
            "source": "rate_limiter",
        }

    def reset(self) -> None:
        """Test-only hook: return the limiter to a fresh, un-throttled state."""
        multiplier = self.backoff_multiplier
        self.__init__(multiplier)


# One process-wide instance - imported by binance_futures_client.py and by
# anything (diagnostics, the snapshot endpoint) that wants to report status.
limiter = BinanceRateLimiter()
