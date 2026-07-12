import time

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.exchanges import manager
from app.exchanges.binance_errors import (
    BinanceError,
    BinanceNetworkError,
    BinanceNotConfigured,
    BinancePermissionError,
    BinanceTimestampError,
)
from app.trading import modes

router = APIRouter(prefix="/api/exchange", tags=["exchange"])

# binance_connected is a network probe; cache it briefly so the dashboard's
# poll loop doesn't hit Binance every 10 seconds.
_CONNECTED_TTL_SECONDS = 30.0
_connected_cache: tuple[float, bool] = (0.0, False)

# Separate cache for the signed account probe (same call, but also keeps the
# safe failure reason - see _binance_account_probe). Independent from
# _connected_cache so existing tests that monkeypatch _binance_connected in
# isolation keep working unchanged.
_account_probe_cache: tuple[float, dict] = (0.0, {"connected": False, "error": None})

# Binance-only public ping, cached separately from manager.status_all()
# (which probes all four exchanges plus funding rates) so the frequently-
# polled /api/trading/mode endpoint can cheaply include public reachability
# without paying for the other adapters on every poll.
_PUBLIC_PING_TTL_SECONDS = 30.0
_public_ping_cache: tuple[float, bool] = (0.0, False)


async def _binance_connected() -> bool:
    """Signed production reachability: True when the configured key can
    read the futures account. Never raises, never leaks error internals."""
    global _connected_cache
    if not modes.binance_configured():
        return False
    checked_at, value = _connected_cache
    if time.time() - checked_at < _CONNECTED_TTL_SECONDS:
        return value
    try:
        from app.api.portfolio import get_read_client
        await get_read_client().get_balances()
        value = True
    except Exception:
        value = False
    _connected_cache = (time.time(), value)
    return value


def _classify_binance_error(exc: Exception) -> str:
    """Map a signed-read failure to one of a small set of safe, non-
    sensitive reasons. Binance's own -2015 code deliberately conflates bad
    key / missing futures permission / IP not whitelisted, so those three
    can't be told apart from the response alone - callers see the combined
    reason rather than a guess."""
    if isinstance(exc, BinanceTimestampError):
        return "Timestamp drift"
    if isinstance(exc, BinancePermissionError):
        if exc.code == -2014:
            return "Invalid key"
        return "Futures permission or IP whitelist issue"
    if isinstance(exc, BinanceNetworkError):
        return "Binance unavailable"
    if isinstance(exc, BinanceNotConfigured):
        return "Binance API is not configured"
    return "Unknown signed-read error"


async def _binance_account_probe() -> dict:
    """Signed account reachability plus a safe categorized failure reason.
    Same signed call as _binance_connected, cached independently so it can
    capture *why* a failure happened without changing that function's
    existing behavior/tests."""
    global _account_probe_cache
    if not modes.binance_configured():
        return {"connected": False, "error": None}
    checked_at, value = _account_probe_cache
    if time.time() - checked_at < _CONNECTED_TTL_SECONDS:
        return value
    try:
        from app.api.portfolio import get_read_client
        await get_read_client().get_balances()
        value = {"connected": True, "error": None}
    except Exception as e:
        value = {"connected": False, "error": _classify_binance_error(e)}
    _account_probe_cache = (time.time(), value)
    return value


async def _binance_public_connected() -> bool:
    """Binance-only public reachability (no signature, no credentials) -
    the 'A' distinction from the signed account probe's 'C'."""
    global _public_ping_cache
    checked_at, value = _public_ping_cache
    if time.time() - checked_at < _PUBLIC_PING_TTL_SECONDS:
        return value
    adapter = manager.get_adapter("binance")
    if adapter is None:
        value = False
    else:
        try:
            result = await adapter.get_exchange_status()
            value = bool(result.get("reachable"))
        except Exception:
            value = False
    _public_ping_cache = (time.time(), value)
    return value


async def binance_connectivity(public_connected: bool | None = None) -> dict:
    """Canonical Binance connectivity shape - the single source every page
    should read instead of computing its own. Distinguishes public market
    reachability (A), key configuration (B) and signed account read (C).
    Never returns keys, secrets, signed URLs or raw request/response
    internals - only booleans and the small safe reason set from
    _classify_binance_error."""
    probe = await _binance_account_probe()
    if public_connected is None:
        public_connected = await _binance_public_connected()
    return {
        "binance_api_key_configured": bool(settings.binance_api_key),
        "binance_api_secret_configured": bool(settings.binance_api_secret),
        "binance_public_connected": public_connected,
        "binance_account_connected": probe["connected"],
        "binance_signed_read_ok": probe["connected"],
        "binance_account_error": probe["error"],
        # back-compat alias - existing consumers of `binance_connected`
        # keep working unchanged.
        "binance_connected": probe["connected"],
    }


@router.get("/status")
async def status():
    """Safe trading status (Phase 23 shape - active_mode, availability,
    lock states, limits, warnings) merged with the legacy per-exchange
    connectivity map the System Status page still reads, plus the
    canonical Binance connectivity fields (binance_connectivity). Never
    returns keys, secrets, signatures or request headers - see
    test_exchange_trading_modes.py. No testnet field is exposed."""
    legacy = await manager.status_all()
    safe = modes.exchange_safe_status()
    public_connected = bool(legacy.get("exchanges", {}).get("binance", {}).get("connected"))
    connectivity = await binance_connectivity(public_connected=public_connected)
    return {
        **safe,
        **connectivity,
        "warnings": modes.status_warnings(),
        **legacy,
    }


@router.get("/balances")
async def balances(exchange: str = "binance"):
    try:
        return {"exchange": exchange, "balances": await manager.balances(exchange)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{exchange} balances unavailable: {e!r}")


@router.get("/positions")
async def positions(exchange: str = "binance"):
    try:
        return {"exchange": exchange, "positions": await manager.positions(exchange)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{exchange} positions unavailable: {e!r}")


@router.get("/open-orders")
async def open_orders(exchange: str = "binance"):
    try:
        return {"exchange": exchange, "open_orders": await manager.open_orders(exchange)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{exchange} open orders unavailable: {e!r}")


@router.get("/risk-check")
async def risk_check():
    return await manager.risk_check()
