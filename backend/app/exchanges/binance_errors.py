"""Typed errors for the Binance Futures trading client.

Every failure a caller may want to branch on (rate limit, timestamp drift,
insufficient balance, permissions, ...) gets its own class, and
`map_binance_error()` turns a raw Binance error payload into the right one.
Messages are safe to surface to the UI: they never contain API keys,
secrets, signatures or request headers.
"""

from __future__ import annotations


class BinanceError(Exception):
    """Base class for anything the Binance client can raise."""

    def __init__(self, message: str, code: int | None = None, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code  # Binance numeric error code (e.g. -2019), if any
        self.status = status  # HTTP status, if any

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code}, status={self.status}, message={self.message!r})"


class BinanceNotConfigured(BinanceError):
    """No API key/secret configured for the requested environment."""


class BinanceNetworkError(BinanceError):
    """Timeout / connection failure before Binance answered."""


class BinanceRateLimitError(BinanceError):
    """HTTP 429/418 or code -1003: back off before retrying."""


class BinanceTimestampError(BinanceError):
    """Code -1021: local clock drifted outside recvWindow."""


class BinanceInsufficientBalance(BinanceError):
    """Code -2019/-2018: margin or balance insufficient for the order."""


class BinanceInvalidSymbol(BinanceError):
    """Code -1121: symbol does not exist on this market."""


class BinanceInvalidLeverage(BinanceError):
    """Code -4028: leverage not valid for this symbol."""


class BinancePermissionError(BinanceError):
    """Code -2015/-2014: bad key, missing futures permission, or IP not
    whitelisted."""


class BinanceOrderRejected(BinanceError):
    """Exchange refused the order for a business-rule reason not covered by
    a more specific class (price filters, reduce-only violations, ...)."""


class BinanceDuplicateOrder(BinanceError):
    """Code -4015 / duplicate newClientOrderId - the idempotency guard
    fired, meaning this exact order was already submitted."""


_CODE_MAP: dict[int, type[BinanceError]] = {
    -1003: BinanceRateLimitError,
    -1021: BinanceTimestampError,
    -1121: BinanceInvalidSymbol,
    -2014: BinancePermissionError,
    -2015: BinancePermissionError,
    -2018: BinanceInsufficientBalance,
    -2019: BinanceInsufficientBalance,
    -4028: BinanceInvalidLeverage,
    -4015: BinanceDuplicateOrder,
}


def map_binance_error(status: int, payload: dict | None) -> BinanceError:
    """Build the most specific error type for a non-2xx Binance response."""
    payload = payload or {}
    code = payload.get("code")
    msg = payload.get("msg") or f"Binance request failed (HTTP {status})"

    if status in (418, 429):
        return BinanceRateLimitError(msg, code=code, status=status)

    if isinstance(code, int) and code in _CODE_MAP:
        return _CODE_MAP[code](msg, code=code, status=status)

    return BinanceOrderRejected(msg, code=code, status=status)
