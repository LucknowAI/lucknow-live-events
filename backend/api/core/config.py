from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    # Only needed for running `alembic upgrade head` locally / in CI.
    # The live API server never uses this — so it is optional for Render/Cloud Run.
    ALEMBIC_DATABASE_URL: str | None = None
    # Engine-only dead-man switch. Empty disables the ping.
    HEALTHCHECK_URL: str = ""

    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""

    STORAGE_TYPE: str = "local"
    LOCAL_STORAGE_PATH: str = "/app/data/snapshots"

    # No fallback signing key: public endpoints work, admin auth stays disabled.
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60

    ADMIN_EMAIL: str = "admin@example.com"
    ADMIN_PASSWORD_HASH: str | None = None

    CORS_ORIGINS: str = "http://localhost:3000"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"


settings = Settings()
