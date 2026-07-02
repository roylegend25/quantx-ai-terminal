from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.security import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest):
    valid_username = body.username == settings.admin_username
    valid_password = bool(settings.admin_password_hash) and verify_password(
        body.password, settings.admin_password_hash
    )

    if not (valid_username and valid_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token(subject=body.username)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
async def me(username: str = Depends(get_current_user)):
    return {"username": username}
