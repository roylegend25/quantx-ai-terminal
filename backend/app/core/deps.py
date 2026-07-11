from fastapi import Header, HTTPException

from app.core.config import settings
from app.core.security import decode_access_token


async def get_current_user(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization.removeprefix("Bearer ").strip()
    username = decode_access_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return username


async def get_current_admin(authorization: str | None = Header(default=None)) -> str:
    """Admin gate for the server-config surface (Phase 24). This deployment
    has exactly one human account - settings.admin_username - so admin ==
    that subject; every other valid token (including the internal scheduler
    service token) is authenticated but NOT authorized: 403."""
    username = await get_current_user(authorization)
    if username != settings.admin_username:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return username
