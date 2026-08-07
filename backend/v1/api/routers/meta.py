"""Liveness, version and effective configuration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from v1 import __version__
from v1.api.deps import ConfigDep, RegistryDep, SettingsDep, require_engine_token
from v1.config.loader import redacted_config

router = APIRouter(tags=["meta"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    tenant: str
    default_profile: str
    profiles_total: int
    profiles_available: int


class VersionResponse(BaseModel):
    engine: str
    config_version: int


@router.get("/health", response_model=HealthResponse, summary="Liveness and readiness")
async def health(config: ConfigDep, settings: SettingsDep, registry: RegistryDep) -> HealthResponse:
    """Reports live only if the config loaded and the LLM registry built.

    Deliberately does not touch the database or any provider: a health check that calls a
    third party fails when the third party does, and then a rate limit looks like an outage.
    """
    profiles = registry.profiles()
    return HealthResponse(
        status="ok",
        version=__version__,
        environment=settings.ENVIRONMENT,
        tenant=config.config.tenant.slug,
        default_profile=registry.default_profile,
        profiles_total=len(profiles),
        profiles_available=sum(1 for profile in profiles if profile.available),
    )


@router.get("/version", response_model=VersionResponse, summary="Engine version")
async def version(config: ConfigDep) -> VersionResponse:
    return VersionResponse(engine=__version__, config_version=config.config.config_version)


@router.get(
    "/config",
    summary="Effective configuration, redacted",
    dependencies=[Depends(require_engine_token)],
)
async def show_config(config: ConfigDep) -> dict[str, Any]:
    """The config as the engine actually resolved it, with secrets removed.

    Answers "which model is this instance really using" without shell access — the question
    that otherwise gets answered by guessing.
    """
    return redacted_config(config)
