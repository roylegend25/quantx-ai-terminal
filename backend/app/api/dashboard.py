from fastapi import APIRouter
import httpx

from app.risk import settings_repository

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

BINANCE_FAPI = "https://fapi.binance.com"

async def symbol_snapshot(client: httpx.AsyncClient, symbol: str):
    ticker = await client.get(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr", params={"symbol": symbol})
    funding = await client.get(f"{BINANCE_FAPI}/fapi/v1/premiumIndex", params={"symbol": symbol})
    oi = await client.get(f"{BINANCE_FAPI}/fapi/v1/openInterest", params={"symbol": symbol})

    ticker.raise_for_status()
    funding.raise_for_status()
    oi.raise_for_status()

    return {
        "symbol": symbol,
        "ticker": ticker.json(),
        "funding": funding.json(),
        "open_interest": oi.json(),
    }

@router.get("")
async def dashboard():
    async with httpx.AsyncClient(timeout=15) as client:
        btc = await symbol_snapshot(client, "BTCUSDT")
        eth = await symbol_snapshot(client, "ETHUSDT")

    risk = settings_repository.get_settings()

    return {
        "mode": "paper",
        "symbols": {
            "BTCUSDT": btc,
            "ETHUSDT": eth,
        },
        "bot": {
            "status": "running",
            "live_trading": False,
            "paper_trading": risk["paper_trading_enabled"],
        },
        "risk": {
            "max_risk_per_trade_pct": risk["max_risk_per_trade_pct"],
            "daily_loss_limit_pct": risk["max_daily_loss_pct"],
            "live_mode_locked": True,
        },
    }
