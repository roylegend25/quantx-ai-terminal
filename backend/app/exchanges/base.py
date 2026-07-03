"""Common contract for read-only exchange adapters.

Hard safety rules enforced structurally, not just by convention:
  - No adapter defines (or may define) an order-placement, cancellation,
    transfer, or withdrawal method. Only read endpoints exist on this
    interface, so there is nothing here that could ever place a real
    trade even if a caller wanted to.
  - Every adapter refuses to even construct unless
    settings.exchange_read_only is True (see ExchangeAdapter.__init__).
  - Credentials come exclusively from environment variables, read directly
    with os.getenv in each adapter - never accepted as request parameters,
    never persisted to the database.
"""
import abc
from dataclasses import dataclass
from typing import Any

from app.core.config import settings


class ExchangeReadOnlyDisabled(RuntimeError):
    """Raised if an adapter is constructed while EXCHANGE_READ_ONLY is not true."""


class ExchangeNotConfigured(RuntimeError):
    """Raised when a read call is attempted without API credentials."""


@dataclass
class PermissionCheck:
    """Result of asking an exchange what its API key is allowed to do."""

    detectable: bool
    withdraw_enabled: bool | None = None
    trade_enabled: bool | None = None
    read_enabled: bool | None = None
    note: str = ""
    raw: dict[str, Any] | None = None

    @property
    def safe(self) -> bool:
        """True unless withdrawal permission was positively detected."""
        return self.withdraw_enabled is not True


class ExchangeAdapter(abc.ABC):
    """Base class for a read-only market-data + account-visibility adapter.

    Subclasses implement the ten read methods below plus check_permissions().
    Nothing else - there is intentionally no place to add order placement.
    """

    name: str = "base"

    def __init__(self):
        if not settings.exchange_read_only:
            raise ExchangeReadOnlyDisabled(
                "EXCHANGE_READ_ONLY must be true to construct any exchange adapter"
            )

    @property
    @abc.abstractmethod
    def configured(self) -> bool:
        """True if the required API credentials are present in the environment."""

    def _require_configured(self) -> None:
        if not self.configured:
            raise ExchangeNotConfigured(f"{self.name} adapter has no API credentials configured")

    @abc.abstractmethod
    async def check_permissions(self) -> PermissionCheck: ...

    @abc.abstractmethod
    async def get_account_info(self) -> dict: ...

    @abc.abstractmethod
    async def get_balances(self) -> list[dict]: ...

    @abc.abstractmethod
    async def get_positions(self) -> list[dict]: ...

    @abc.abstractmethod
    async def get_open_orders(self) -> list[dict]: ...

    @abc.abstractmethod
    async def get_funding_rate(self, symbol: str) -> dict: ...

    @abc.abstractmethod
    async def get_open_interest(self, symbol: str) -> dict: ...

    @abc.abstractmethod
    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict: ...

    @abc.abstractmethod
    async def get_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]: ...

    @abc.abstractmethod
    async def get_candles(self, symbol: str, interval: str = "5m", limit: int = 100) -> list[dict]: ...

    @abc.abstractmethod
    async def get_exchange_status(self) -> dict: ...
