from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from api.core.security import decode_token


_bearer = HTTPBearer()


def get_current_admin(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Dependency that validates the JWT and returns its payload.
    Raises 401 if token is missing, invalid, or expired.
    """
    try:
        payload = decode_token(creds.credentials)
        if payload.get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
