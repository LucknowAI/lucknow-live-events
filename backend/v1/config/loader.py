"""Load, override, validate and secret-resolve the instance configuration.

Order of operations, and why:

1. **Read the YAML.** One file, explicitly located by `V1_CONFIG_PATH`.
2. **Apply narrow env overrides** (`V1_LLM_DEFAULT_PROFILE`, `V1_LLM_BUDGET_LEDGER`,
   `V1_LLM_FIXTURE_DIR`) *before* validation, so an override that produces an invalid
   config fails the same way a bad file does.
3. **Validate** against `EngineConfig`. Any failure aborts with the file path and the
   offending key path in the message.
4. **Resolve secrets eagerly, but only for profiles that will actually be used.** A
   contributor with one API key must still be able to boot; a missing key for the
   *default* profile must not be discovered on the first paid call at 3am.

Secrets are resolved from, in order: `V1_<NAME>` env, `<NAME>` env, then
`<V1_SECRETS_DIR>/<NAME>` as a file (the Cloud Run / Kubernetes mount convention).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from v1.config.schema import COSTLESS_ADAPTERS, EngineConfig, LLMProfileConfig
from v1.config.settings import V1Settings, get_settings
from v1.contracts.errors import ConfigError, ProfileUnavailable, SecretMissing

REDACTED = "***redacted***"


class SecretResolver:
    """Resolves `api_key_ref` names to values. Never logs or returns the value twice."""

    def __init__(self, *, env_prefix: str = "V1_", secrets_dir: Path | None = None) -> None:
        self._env_prefix = env_prefix
        self._secrets_dir = secrets_dir

    def resolve(self, name: str) -> str | None:
        value = os.getenv(f"{self._env_prefix}{name}") or os.getenv(name)
        if value:
            return value.strip()
        if self._secrets_dir is not None:
            candidate = self._secrets_dir / name
            try:
                if candidate.is_file():
                    return candidate.read_text(encoding="utf-8").strip() or None
            except OSError:
                # An unreadable mount is a config problem, not a secret value problem.
                return None
        return None

    def describe_sources(self, name: str) -> str:
        """Human-readable list of the places we looked. Used in error messages only."""
        places = [f"${self._env_prefix}{name}", f"${name}"]
        if self._secrets_dir is not None:
            places.append(str(self._secrets_dir / name))
        return ", ".join(places)


@dataclass(frozen=True)
class ProfileAvailability:
    """Whether a configured profile can be used right now, and why not if it cannot."""

    name: str
    available: bool
    reason: str | None = None
    in_use: bool = False
    """True when this profile is wired to `llm.default` or an `llm.tasks` entry. An unusable
    profile that is *in use* is a louder problem than an unusable spare one, and the registry
    logs it as such — this flag is how that travels without `config` importing `platform`."""


@dataclass(frozen=True)
class LoadedConfig:
    """The validated config plus everything derived from the environment around it."""

    config: EngineConfig
    path: Path
    api_keys: dict[str, str]
    """profile name -> resolved API key. Absent for keyless/local/costless profiles."""
    availability: dict[str, ProfileAvailability]

    def api_key_for(self, profile_name: str) -> str | None:
        return self.api_keys.get(profile_name)

    def require_available(self, profile_name: str) -> None:
        entry = self.availability.get(profile_name)
        if entry is None:
            raise ConfigError(f"unknown profile {profile_name!r}")
        if not entry.available:
            raise ProfileUnavailable(
                f"profile {profile_name!r} is not usable: {entry.reason}",
                profile=profile_name,
            )


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}", path=str(path)) from exc
    except OSError as exc:
        raise ConfigError(f"could not read config file {path}: {exc}", path=str(path)) from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file {path} is not valid YAML: {exc}", path=str(path)) from exc

    if data is None:
        raise ConfigError(f"config file {path} is empty", path=str(path))
    if not isinstance(data, dict):
        raise ConfigError(
            f"config file {path} must contain a mapping at the top level, got "
            f"{type(data).__name__}",
            path=str(path),
        )
    return data


def _apply_env_overrides(data: dict[str, Any], settings: V1Settings) -> dict[str, Any]:
    llm = data.get("llm")
    if not isinstance(llm, dict):
        # Leave it alone; validation will produce the proper error.
        return data

    if settings.LLM_FORCE_PROFILE:
        # Every task, one profile. Clearing `tasks` is the point: leaving it would keep
        # routing extraction to whatever the file names, which defeats the override.
        llm["default"] = settings.LLM_FORCE_PROFILE
        llm["tasks"] = {}
    elif settings.LLM_DEFAULT_PROFILE:
        llm["default"] = settings.LLM_DEFAULT_PROFILE
    if settings.LLM_BUDGET_LEDGER:
        budget = llm.setdefault("budget", {})
        if isinstance(budget, dict):
            budget["ledger"] = settings.LLM_BUDGET_LEDGER
    if settings.LLM_FIXTURE_DIR:
        profiles = llm.get("profiles")
        if isinstance(profiles, dict):
            for profile in profiles.values():
                if isinstance(profile, dict) and profile.get("adapter") == "fixture":
                    profile["fixture_dir"] = str(settings.LLM_FIXTURE_DIR)
    return data


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"invalid engine config {path}:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)


def _needs_key(profile: LLMProfileConfig) -> bool:
    return profile.api_key_ref is not None and profile.adapter not in COSTLESS_ADAPTERS


def _resolve_keys(
    config: EngineConfig, resolver: SecretResolver, *, strict: bool
) -> tuple[dict[str, str], dict[str, ProfileAvailability]]:
    keys: dict[str, str] = {}
    availability: dict[str, ProfileAvailability] = {}
    in_use = config.llm.in_use_profiles

    for name, profile in config.llm.profiles.items():
        if not _needs_key(profile):
            availability[name] = ProfileAvailability(name=name, available=True)
            continue

        assert profile.api_key_ref is not None  # guarded by _needs_key
        value = resolver.resolve(profile.api_key_ref)
        if value:
            keys[name] = value
            availability[name] = ProfileAvailability(name=name, available=True)
            continue

        reason = (
            f"secret {profile.api_key_ref!r} not found (looked in: "
            f"{resolver.describe_sources(profile.api_key_ref)})"
        )
        if name in in_use and strict:
            # This profile is wired to `default` or a `tasks` entry — it *will* be called,
            # so outside development a missing key is a startup failure, not a 3am surprise.
            raise SecretMissing(
                f"profile {name!r} is selected by llm.default or llm.tasks but its {reason}",
                profile=name,
                api_key_ref=profile.api_key_ref,
            )
        if name in in_use:
            # Development: a fresh clone with no keys must still boot, on fixtures or a local
            # model. The profile is marked unusable with its reason, `GET /llm/profiles`
            # reports it, and calling it raises `ProfileUnavailable` naming the missing
            # variable — loud, but not a wall in front of the first run.
            #
            # The warning is returned as *data* (`in_use=True` below) rather than logged here.
            # `config` must not import `platform`, or the two form an import cycle at package
            # level: `platform.logging` already reads `config.settings`. The registry emits it.
            availability[name] = ProfileAvailability(
                name=name, available=False, reason=reason, in_use=True
            )
            continue
        availability[name] = ProfileAvailability(name=name, available=False, reason=reason)

    return keys, availability


def load_config(
    path: Path | None = None,
    *,
    settings: V1Settings | None = None,
    resolver: SecretResolver | None = None,
    strict_secrets: bool | None = None,
) -> LoadedConfig:
    """Load and fully validate the engine config. Raises `ConfigError` on any problem.

    `strict_secrets` decides whether a missing secret for an *in-use* profile is fatal.
    It defaults to "fatal everywhere except development", so a deployed engine cannot start
    half-configured while a fresh clone with no API keys still boots on fixtures.
    """
    settings = settings or get_settings()
    config_path = Path(path) if path is not None else settings.CONFIG_PATH
    resolver = resolver or SecretResolver(secrets_dir=settings.SECRETS_DIR)
    strict = (not settings.is_development) if strict_secrets is None else strict_secrets

    data = _apply_env_overrides(_read_yaml(config_path), settings)
    try:
        config = EngineConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            _format_validation_error(config_path, exc), path=str(config_path)
        ) from exc

    keys, availability = _resolve_keys(config, resolver, strict=strict)
    return LoadedConfig(config=config, path=config_path, api_keys=keys, availability=availability)


@lru_cache(maxsize=1)
def get_config() -> LoadedConfig:
    """Process-wide config singleton. Cleared in tests via `get_config.cache_clear()`."""
    return load_config()


def redacted_config(loaded: LoadedConfig) -> dict[str, Any]:
    """Config as JSON for `GET /config`, with every secret-bearing field replaced.

    Only `api_key_ref` *names* are ever in the file, so there is nothing to leak from the
    YAML itself — but resolved values live in the same object, and `extra_body` is a
    free-form dict that a config author could put a token in. Both are handled.
    """
    payload = loaded.config.model_dump(mode="json")
    for name, profile in payload.get("llm", {}).get("profiles", {}).items():
        if profile.get("extra_body"):
            profile["extra_body"] = {key: REDACTED for key in profile["extra_body"]}
        entry = loaded.availability.get(name)
        profile["_available"] = entry.available if entry else False
        profile["_unavailable_reason"] = entry.reason if entry else "not resolved"
        profile["_api_key_present"] = name in loaded.api_keys
    payload["_config_path"] = str(loaded.path)
    return payload
