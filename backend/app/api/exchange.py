import time

from fastapi import APIRouter, HTTPException

from app.exchanges import manager
from app.trading import modes

router = APIRouter(prefix="/api/exchange", tags=["exchange"])

# binance_connected is a network probe; cache it briefly so the dashboard's
# poll loop doesn't hit Binance every 10 seconds.
_CONNECTED_TTL_SECONDS = 30.0
_connected_cache: tuple[float, bool] = (0.0, False)


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


@router.get("/status")
async def status():
    """Safe trading status (Phase 23 shape - active_mode, availability,
    lock states, limits, warnings) merged with the legacy per-exchange
    connectivity map the System Status page still reads. Never returns
    keys, secrets, signatures or request headers - see
    test_exchange_trading_modes.py. No testnet field is exposed."""
    legacy = await manager.status_all()
    safe = modes.exchange_safe_status()
    return {
        **safe,
        "binance_connected": await _binance_connected(),
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
