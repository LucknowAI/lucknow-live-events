from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from api.core.config import settings
from api.core.logging import get_logger
from api.core.security import create_access_token, verify_password
from api.schemas.admin import LoginRequest, TokenResponse

router = APIRouter()
log = get_logger(__name__)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    if payload.email != settings.ADMIN_EMAIL:
        log.warning("admin.login_failed", email=payload.email, reason="unknown_email")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not settings.ADMIN_PASSWORD_HASH:
        log.error("admin.login_unconfigured", email=payload.email)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin password not configured. Set ADMIN_PASSWORD_HASH in env.",
        )

    if not verify_password(payload.password, settings.ADMIN_PASSWORD_HASH):
        log.warning("admin.login_failed", email=payload.email, reason="invalid_password")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token({"sub": payload.email, "role": "admin"})
    log.info("admin.login_success", email=payload.email)
    return TokenResponse(access_token=token)
