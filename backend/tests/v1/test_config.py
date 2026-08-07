"""Config validation: the failures that must happen at startup, not at 3am.

Every case here is a real mistake someone can make in `instance.yaml`, and each one has a
cost if it is discovered late: an un-priced paid profile means the budget cap silently does
nothing; a profile claiming `native_schema` on a Groq Llama model means we trust enforcement
we never had; a literal key in the YAML means a secret in git history.
"""

from __future__ import annotations

import pytest

from v1.config.loader import SecretResolver, load_config, redacted_config
from v1.config.settings import V1Settings
from v1.contracts.errors import ConfigError, ProfileUnavailable, SecretMissing


def reference_config_path():
    from v1 import BACKEND_ROOT

    return BACKEND_ROOT / "v1" / "config" / "instance.yaml"


def test_reference_config_loads(settings: V1Settings) -> None:
    """The shipped instance.yaml must be valid, and must boot with no API keys at all.

    A fresh clone has no keys. If the reference config could not load without them, the
    documented "develop on fixtures, live providers opt-in" workflow would be fiction.
    """
    loaded = load_config(
        reference_config_path(),
        settings=settings,
        resolver=SecretResolver(env_prefix="NO_SUCH_PREFIX_", secrets_dir=None),
    )
    llm = loaded.config.llm
    assert llm.default in llm.profiles
    # Every task target resolves, and every paid profile is priced with provenance.
    for task in llm.tasks:
        assert llm.profile_for_task(task) in llm.profiles
    for name, profile in llm.profiles.items():
        if profile.is_paid:
            assert profile.pricing is not None, name
            assert profile.pricing.source and profile.pricing.as_of
    # Keyless clone: the costless profiles are usable, the paid ones report why they are not.
    assert loaded.availability["ci_fixture"].available is True
    assert loaded.availability["ci_mock"].available is True
    assert loaded.availability["gemini_flash_lite"].available is False


def test_reference_config_refuses_to_load_without_keys_outside_development(tmp_path) -> None:
    """The same missing key is fatal in a deployed environment.

    Development gets a warning so a fresh clone can run on fixtures; staging and production
    must not start half-configured and discover it on the first paid call.
    """
    strict_settings = V1Settings(
        _env_file=None,
        CONFIG_PATH=reference_config_path(),
        ENVIRONMENT="production",
        SECRETS_DIR=tmp_path / "nowhere",
    )
    with pytest.raises(SecretMissing) as exc:
        load_config(
            reference_config_path(),
            settings=strict_settings,
            resolver=SecretResolver(env_prefix="NO_SUCH_PREFIX_", secrets_dir=None),
        )
    assert "GEMINI_API_KEY" in str(exc.value)


def test_paid_profile_without_pricing_is_rejected(
    settings: V1Settings, write_config, empty_resolver
) -> None:
    path = write_config({"llm": {"profiles": {"paid_gemini": {"pricing": None}}}})
    with pytest.raises(ConfigError) as exc:
        load_config(path, settings=settings, resolver=empty_resolver)
    assert "pricing" in str(exc.value)


def test_groq_llama_cannot_claim_native_schema(
    settings: V1Settings, write_config, empty_resolver
) -> None:
    """The correction that motivated the fifth strategy.

    Groq honours `strict: true` only on openai/gpt-oss-*; on a Llama model it is ignored.
    Declaring native_schema there would have the port trust enforcement it never got.
    """
    path = write_config(
        {
            "llm": {
                "profiles": {
                    "groq_bad": {
                        "adapter": "openai_compatible",
                        "base_url": "https://api.groq.com/openai/v1",
                        "model": "llama-3.3-70b-versatile",
                        "api_key_ref": "GROQ_KEY",
                        "structured_output": "native_schema",
                        "pricing": {
                            "input_usd_per_mtok": 0.59,
                            "output_usd_per_mtok": 0.79,
                            "source": "test",
                            "as_of": "2026-08-06",
                        },
                    }
                }
            }
        }
    )
    with pytest.raises(ConfigError) as exc:
        load_config(path, settings=settings, resolver=empty_resolver)
    assert "gpt-oss" in str(exc.value)


def test_groq_gpt_oss_may_claim_native_schema(
    settings: V1Settings, write_config, empty_resolver
) -> None:
    path = write_config(
        {
            "llm": {
                "profiles": {
                    "groq_ok": {
                        "adapter": "openai_compatible",
                        "base_url": "https://api.groq.com/openai/v1",
                        "model": "openai/gpt-oss-20b",
                        "api_key_ref": "GROQ_KEY",
                        "structured_output": "native_schema",
                        "pricing": {
                            "input_usd_per_mtok": 0.075,
                            "output_usd_per_mtok": 0.30,
                            "source": "test",
                            "as_of": "2026-08-06",
                        },
                    }
                }
            }
        }
    )
    loaded = load_config(path, settings=settings, resolver=empty_resolver)
    assert loaded.config.llm.profiles["groq_ok"].structured_output == "native_schema"


def test_literal_secret_in_api_key_ref_is_rejected(
    settings: V1Settings, write_config, empty_resolver
) -> None:
    path = write_config(
        {"llm": {"profiles": {"paid_gemini": {"api_key_ref": "AIzaSyFakeKeyValue123"}}}}
    )
    with pytest.raises(ConfigError) as exc:
        load_config(path, settings=settings, resolver=empty_resolver)
    assert "environment variable name" in str(exc.value)


def test_unknown_default_profile_is_rejected(
    settings: V1Settings, write_config, empty_resolver
) -> None:
    path = write_config({"llm": {"default": "does_not_exist"}})
    with pytest.raises(ConfigError) as exc:
        load_config(path, settings=settings, resolver=empty_resolver)
    assert "does_not_exist" in str(exc.value)


def test_unknown_key_is_rejected(settings: V1Settings, write_config, empty_resolver) -> None:
    """A typo must fail, not be silently ignored — the whole point of `extra="forbid"`."""
    path = write_config({"llm": {"profiles": {"mock_default": {"temperatur": 0.5}}}})
    with pytest.raises(ConfigError):
        load_config(path, settings=settings, resolver=empty_resolver)


def test_degrade_to_a_paid_profile_is_rejected(
    settings: V1Settings, write_config, empty_resolver
) -> None:
    path = write_config(
        {"llm": {"budget": {"on_exceeded": "degrade_to_fixture", "degrade_profile": "paid_gemini"}}}
    )
    with pytest.raises(ConfigError) as exc:
        load_config(path, settings=settings, resolver=empty_resolver)
    assert "costless" in str(exc.value)


def test_missing_secret_for_in_use_profile_is_fatal(
    settings: V1Settings, write_config, empty_resolver
) -> None:
    """A profile wired to `default` or `tasks` *will* be called; a missing key is a boot error.

    `strict_secrets=True` is what a deployed environment gets by default.
    """
    path = write_config({"llm": {"default": "paid_gemini"}})
    with pytest.raises(SecretMissing) as exc:
        load_config(path, settings=settings, resolver=empty_resolver, strict_secrets=True)
    assert "TEST_GEMINI_KEY" in str(exc.value)


def test_missing_secret_for_unused_profile_only_marks_it_unavailable(
    settings: V1Settings, write_config, empty_resolver
) -> None:
    """A contributor with one key must still be able to boot the engine."""
    loaded = load_config(write_config(), settings=settings, resolver=empty_resolver)
    assert loaded.availability["mock_default"].available is True
    assert loaded.availability["paid_gemini"].available is False
    assert "TEST_GEMINI_KEY" in (loaded.availability["paid_gemini"].reason or "")
    with pytest.raises(ProfileUnavailable):
        loaded.require_available("paid_gemini")


def test_file_mounted_secret_is_resolved(loaded_config) -> None:
    assert loaded_config.api_key_for("paid_gemini") == "test-key-value"
    assert loaded_config.availability["paid_gemini"].available is True


def test_env_override_redirects_the_default_profile(tmp_path, write_config, keyed_resolver) -> None:
    """`V1_LLM_DEFAULT_PROFILE` must be validated exactly like the file value."""
    path = write_config()
    overridden = V1Settings(
        _env_file=None,
        CONFIG_PATH=path,
        SECRETS_DIR=tmp_path / "secrets",
        LLM_DEFAULT_PROFILE="fixtures",
    )
    loaded = load_config(path, settings=overridden, resolver=keyed_resolver)
    assert loaded.config.llm.default == "fixtures"

    bad = V1Settings(
        _env_file=None,
        CONFIG_PATH=path,
        SECRETS_DIR=tmp_path / "secrets",
        LLM_DEFAULT_PROFILE="nope",
    )
    with pytest.raises(ConfigError):
        load_config(path, settings=bad, resolver=keyed_resolver)


def test_force_profile_also_clears_task_routing(tmp_path, write_config, keyed_resolver) -> None:
    """`LLM_DEFAULT_PROFILE` alone cannot redirect a run — `llm.tasks` still wins per task.

    That is the gap `LLM_FORCE_PROFILE` exists to close: "run everything on the local model"
    must not keep calling a paid provider for extraction.
    """
    path = write_config({"llm": {"tasks": {"extraction": "paid_gemini"}}})

    partial = V1Settings(
        _env_file=None,
        CONFIG_PATH=path,
        SECRETS_DIR=tmp_path / "secrets",
        LLM_DEFAULT_PROFILE="fixtures",
    )
    loaded = load_config(path, settings=partial, resolver=keyed_resolver)
    assert loaded.config.llm.default == "fixtures"
    assert loaded.config.llm.profile_for_task("extraction") == "paid_gemini"  # still paid

    forced = V1Settings(
        _env_file=None,
        CONFIG_PATH=path,
        SECRETS_DIR=tmp_path / "secrets",
        LLM_FORCE_PROFILE="fixtures",
    )
    loaded = load_config(path, settings=forced, resolver=keyed_resolver)
    assert loaded.config.llm.default == "fixtures"
    assert loaded.config.llm.tasks == {}
    assert loaded.config.llm.profile_for_task("extraction") == "fixtures"
    assert loaded.config.llm.in_use_profiles == {"fixtures"}


def test_force_profile_wins_over_default_profile(tmp_path, write_config, keyed_resolver) -> None:
    path = write_config()
    settings = V1Settings(
        _env_file=None,
        CONFIG_PATH=path,
        SECRETS_DIR=tmp_path / "secrets",
        LLM_DEFAULT_PROFILE="paid_gemini",
        LLM_FORCE_PROFILE="fixtures",
    )
    loaded = load_config(path, settings=settings, resolver=keyed_resolver)
    assert loaded.config.llm.default == "fixtures"


def test_unusable_in_use_profile_is_flagged_for_the_logger(
    settings: V1Settings, write_config, empty_resolver
) -> None:
    """`config` must not import `platform`, so the warning travels as data, not a log call."""
    path = write_config({"llm": {"default": "paid_gemini"}})
    loaded = load_config(path, settings=settings, resolver=empty_resolver)

    entry = loaded.availability["paid_gemini"]
    assert entry.available is False
    assert entry.in_use is True
    # A spare profile with no key is not the same problem and is not flagged as one.
    assert loaded.availability["fixtures"].in_use is False


def test_redacted_config_never_contains_a_key(loaded_config) -> None:
    payload = redacted_config(loaded_config)
    assert "test-key-value" not in str(payload)
    assert payload["llm"]["profiles"]["paid_gemini"]["_api_key_present"] is True
    # The reference name is safe and useful — it tells an operator which env var to set.
    assert payload["llm"]["profiles"]["paid_gemini"]["api_key_ref"] == "TEST_GEMINI_KEY"


def test_missing_config_file_message_names_the_path(settings: V1Settings, tmp_path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError) as exc:
        load_config(missing, settings=settings)
    assert str(missing) in str(exc.value)
