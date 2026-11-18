"""Admin credentials are server settings; missing signing keys must fail closed."""

import os
from datetime import timedelta
from typing import Annotated

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import JWTError, jwt


@pytest.fixture
def auth(monkeypatch):
    # Synthetic values are confined to tests; no real database or credentials are used.
    if not os.environ.get("DATABASE_URL"):
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://unused/unused_test")
    from api.core import security
    from api.core.config import settings
    from api.routers.admin.auth import router

    monkeypatch.setattr(settings, "JWT_SECRET", "test-only-signing-key-never-deploy")
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setattr(
        settings,
        "ADMIN_PASSWORD_HASH",
        bcrypt.hashpw(b"test-only-password", bcrypt.gensalt(rounds=4)).decode(),
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield settings, security, client


@pytest.mark.parametrize("key", ["", "   "])
def test_missing_signing_key_disables_login_and_verification(auth, monkeypatch, key):
    settings, security, client = auth
    previously_issued = security.create_access_token({"role": "admin"})
    monkeypatch.setattr(settings, "JWT_SECRET", key)
    response = client.post(
        "/login", json={"email": settings.ADMIN_EMAIL, "password": "test-only-password"}
    )
    assert response.status_code == 503
    with pytest.raises(ValueError, match="JWT_SECRET"):
        security.create_access_token({"role": "admin"})
    with pytest.raises(JWTError, match="JWT_SECRET"):
        security.decode_token(previously_issued)


def test_password_login_still_issues_admin_token(auth):
    settings, security, client = auth
    response = client.post(
        "/login", json={"email": settings.ADMIN_EMAIL, "password": "test-only-password"}
    )
    assert response.status_code == 200
    payload = security.decode_token(response.json()["access_token"])
    assert payload["role"] == "admin"
    assert payload["sub"] == settings.ADMIN_EMAIL


@pytest.mark.parametrize(
    ("email", "password"),
    [("other@example.com", "test-only-password"), ("admin@example.com", "wrong-password")],
)
def test_invalid_credentials_are_rejected(auth, email, password):
    _, _, client = auth
    assert client.post("/login", json={"email": email, "password": password}).status_code == 401


def test_missing_password_hash_disables_login(auth, monkeypatch):
    settings, _, client = auth
    monkeypatch.setattr(settings, "ADMIN_PASSWORD_HASH", None)
    assert (
        client.post(
            "/login", json={"email": settings.ADMIN_EMAIL, "password": "test-only-password"}
        ).status_code
        == 503
    )


def test_admin_dependency_rejects_missing_invalid_expired_and_non_admin_tokens(auth):
    from fastapi import Depends

    from api.core.deps import get_current_admin

    _, security, _ = auth
    app = FastAPI()

    @app.get("/protected")
    def protected(admin: Annotated[dict, Depends(get_current_admin)]):
        return {"role": admin["role"]}

    valid = security.create_access_token({"role": "admin"})
    invalid = jwt.encode({"role": "admin"}, "different-test-only-key", algorithm="HS256")
    expired = security.create_access_token({"role": "admin"}, timedelta(seconds=-1))
    non_admin = security.create_access_token({"role": "viewer"})
    with TestClient(app) as client:
        assert client.get("/protected").status_code in (401, 403)
        for token, expected in [(valid, 200), (invalid, 401), (expired, 401), (non_admin, 403)]:
            assert (
                client.get("/protected", headers={"Authorization": f"Bearer {token}"}).status_code
                == expected
            )


def test_settings_have_no_usable_default_signing_key(auth, monkeypatch):
    from api.core.config import Settings

    monkeypatch.delenv("JWT_SECRET", raising=False)
    assert Settings(_env_file=None).JWT_SECRET == ""


def test_missing_signing_key_keeps_public_health_available(auth, monkeypatch):
    from api.main import app

    settings, _, _ = auth
    monkeypatch.setattr(settings, "JWT_SECRET", "")
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True}
        assert client.get("/api/v1/admin/stats").status_code in (401, 403)
