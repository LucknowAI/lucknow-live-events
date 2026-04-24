from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    # Only needed for running `alembic upgrade head` locally / in CI.
    # The live API server never uses this — so it is optional for Render/Cloud Run.
    ALEMBIC_DATABASE_URL: str | None = None
    REDIS_URL: str = "redis://redis:6379/0"

    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-3.0-flash"
    AI_MODE: str = "gemini"  # gemini | mock
    AI_FALLBACK_TO_MOCK: bool = True

    STORAGE_TYPE: str = "local"
    LOCAL_STORAGE_PATH: str = "/app/data/snapshots"

    JWT_SECRET: str = "change_me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60

    ADMIN_EMAIL: str = "admin@example.com"
    ADMIN_PASSWORD_HASH: str | None = None

    CORS_ORIGINS: str = "http://localhost:3000"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"


settings = Settings()

