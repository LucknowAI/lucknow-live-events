from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from api.core.config import settings
from api.core.security import create_access_token, verify_password
from api.schemas.admin import LoginRequest, TokenResponse


router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    if payload.email != settings.ADMIN_EMAIL:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not settings.ADMIN_PASSWORD_HASH:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin password not configured. Set ADMIN_PASSWORD_HASH in env.",
        )

    if not verify_password(payload.password, settings.ADMIN_PASSWORD_HASH):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token({"sub": payload.email, "role": "admin"})
    return TokenResponse(access_token=token)
