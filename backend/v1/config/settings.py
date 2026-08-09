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

    ENVIRONMENT: str = "production"  # development | staging | production
    """Defaults to the **strict** setting, deliberately.

    `development` relaxes real controls: missing API keys become warnings, the engine
    token becomes optional, and in-memory spend ledgers are allowed. Defaulting to it
    meant every production safety check was opt-in — deploy a container without setting
    this variable and you get the permissive mode silently, which is the exact situation
    those checks exist to prevent.

    Fail-safe instead: an unconfigured process is strict and refuses to start until its
    token and durable ledgers are set. Local work opts *down* by setting
    `V1_ENVIRONMENT=development`, which `backend/.env.example` does on its first line."""

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

    # --- search (Phase 2) ---
    SEARCH_FORCE_PROVIDER: str | None = None
    """Collapses `search.chain` to this one provider. The way to run a whole discovery
    pass on recorded fixtures without editing the YAML: `V1_SEARCH_FORCE_PROVIDER=fixture`.

    Note it replaces the chain rather than reordering it. A "preferred provider" that still
    falls back to the paid chain would defeat the purpose — the reason to force `fixture`
    is precisely that nothing should reach the network."""

    SEARCH_BUDGET_LEDGER: str | None = None  # memory | postgres
    SEARCH_CACHE_BACKEND: str | None = None  # memory | postgres
    SEARCH_FIXTURE_DIR: Path | None = None
    SEARCH_ALLOW_LIVE: bool = False
    """Second key for SERP spend, mirroring `LLM_ALLOW_LIVE`. A configured key alone is
    not consent to spend it: keys end up in a shared `.env` and a discovery run issues
    hundreds of queries without a human watching."""

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT.lower() == "development"


@lru_cache(maxsize=1)
def get_settings() -> V1Settings:
    return V1Settings()
