from fastapi import APIRouter
from datetime import datetime, timezone

router = APIRouter(prefix="/api/bot", tags=["bot"])

BOT_STATE = {
    "status": "running",
    "mode": "paper",
    "live_trading_enabled": False,
    "last_action": "init",
    "updated_at": datetime.now(timezone.utc).isoformat(),
}

def update_state(action: str):
    BOT_STATE["last_action"] = action
    BOT_STATE["updated_at"] = datetime.now(timezone.utc).isoformat()

@router.get("/status")
async def bot_status():
    return BOT_STATE

@router.post("/start")
async def start_bot():
    BOT_STATE["status"] = "running"
    update_state("start")
    return {"ok": True, "message": "Bot started", "state": BOT_STATE}

@router.post("/pause")
async def pause_bot():
    BOT_STATE["status"] = "paused"
    update_state("pause")
    return {"ok": True, "message": "Bot paused", "state": BOT_STATE}

@router.post("/stop")
async def stop_bot():
    BOT_STATE["status"] = "stopped"
    update_state("stop")
    return {"ok": True, "message": "Bot stopped", "state": BOT_STATE}

@router.post("/paper")
async def paper_mode():
    BOT_STATE["mode"] = "paper"
    update_state("paper")
    return {"ok": True, "message": "Paper mode enabled", "state": BOT_STATE}

@router.post("/live")
async def live_mode():
    BOT_STATE["mode"] = "paper"
    BOT_STATE["live_trading_enabled"] = False
    update_state("live_blocked")
    return {
        "ok": False,
        "message": "Live mode is locked until API keys, risk limits, and execution safeguards are configured.",
        "state": BOT_STATE,
    }
