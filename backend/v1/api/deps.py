"""Request dependencies: the engine token gate and access to app state.

**Why these endpoints are gated at all.** `POST /llm/complete` and `POST /llm/selftest`
spend money against our provider keys and accept free-text prompts. An unauthenticated
version of them is a funded, anonymous prompt relay. So a shared secret in
`X-Engine-Token` is required, compared in constant time.

**Why development is different.** Requiring a token to run `/health` locally is the kind of
friction that ends with the check being commented out. In development with no token
configured the gate is open and every gated call logs a warning. Outside development the app
refuses to start without a token at all (`v1.api.app`), so the open path cannot reach a
deployed environment.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from v1.config.loader import LoadedConfig
from v1.config.settings import V1Settings
from v1.platform.logging import get_logger
from v1.providers.llm.registry import LLMRegistry

logger = get_logger("v1.api.auth")

TOKEN_HEADER = "X-Engine-Token"


def get_settings_dep(request: Request) -> V1Settings:
    return request.app.state.settings


def get_config_dep(request: Request) -> LoadedConfig:
    return request.app.state.config


def get_registry_dep(request: Request) -> LLMRegistry:
    return request.app.state.llm_registry


def require_engine_token(
    request: Request,
    x_engine_token: Annotated[str | None, Header(alias=TOKEN_HEADER)] = None,
) -> None:
    settings: V1Settings = request.app.state.settings
    expected = settings.ENGINE_TOKEN

    if not expected:
        # Only reachable in development — enforced at startup.
        logger.warning(
            "engine_token_not_configured",
            path=request.url.path,
            detail="gated endpoint served without authentication (development only)",
        )
        return

    if not x_engine_token or not secrets.compare_digest(x_engine_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"missing or invalid {TOKEN_HEADER}",
        )


Gated = Depends(require_engine_token)
SettingsDep = Annotated[V1Settings, Depends(get_settings_dep)]
ConfigDep = Annotated[LoadedConfig, Depends(get_config_dep)]
RegistryDep = Annotated[LLMRegistry, Depends(get_registry_dep)]
