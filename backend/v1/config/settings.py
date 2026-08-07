"""Process-level environment for the V1 engine.

Deliberately small. Everything that describes *the instance* (tenant, timezone, model
profiles, taxonomy) lives in the YAML config file — ADR-013. What lives here is only
what describes *this process*: where the config file is, where secrets are, how to log,
and how to reach the database.

Own env prefix `V1_` so the engine can never accidentally read the V0 pipeline's
settings, and vice versa.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_CONFIG = Path(__file__).with_name("instance.yaml")


class V1Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="V1_", env_file=".env", extra="ignore")

    CONFIG_PATH: Path = _DEFAULT_CONFIG
    ENVIRONMENT: str = "development"  # development | staging | production
    SERVICE_NAME: str = "engine-v1"

    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "console"  # console | json

    DATABASE_URL: str | None = None
    """Async DSN (postgresql+asyncpg://...). Optional: the engine runs without a DB when
    the budget ledger is `memory`, which is what CI and `/llm/complete` dry runs use."""

    SECRETS_DIR: Path = Path("/secrets")
    """Directory of file-mounted secrets (Cloud Run / K8s style), one file per name."""

    ENGINE_TOKEN: str | None = None
    """Shared secret required in `X-Engine-Token` for the spending endpoints. Outside
    development, the app refuses to start without it."""

    # --- narrow overrides, so a run can be redirected without editing the YAML ---
    LLM_DEFAULT_PROFILE: str | None = None
    """Replaces `llm.default`. Per-task entries in `llm.tasks` still win for their task."""

    LLM_FORCE_PROFILE: str | None = None
    """Routes *every* task to one profile: sets `llm.default` and clears `llm.tasks`.

    `LLM_DEFAULT_PROFILE` alone is not enough to redirect a whole run — a `tasks` entry
    still binds its task to whatever the file says, so "run everything on the local model"
    would silently keep calling a paid provider for extraction. This is the switch that
    actually means it."""

    LLM_BUDGET_LEDGER: str | None = None  # memory | postgres
    LLM_FIXTURE_DIR: Path | None = None
    LLM_ALLOW_LIVE: bool = False
    """Guard for the live conformance run. `POST /llm/selftest?live=true` and the
    live-provider tests refuse to spend money unless this is explicitly on."""

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT.lower() == "development"


@lru_cache(maxsize=1)
def get_settings() -> V1Settings:
    return V1Settings()
