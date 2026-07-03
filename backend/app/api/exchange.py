from fastapi import APIRouter, HTTPException

from app.exchanges import manager

router = APIRouter(prefix="/api/exchange", tags=["exchange"])


@router.get("/status")
async def status():
    return await manager.status_all()


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
