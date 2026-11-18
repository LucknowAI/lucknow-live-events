"""Render Compose with isolated synthetic inputs; never read a developer's env file."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", [".env.example", ".env.example.dev", ".env.example.prod"])
def test_templates_do_not_ship_credentials(name):
    values = dotenv_values(ROOT / name)
    for key in ["JWT_SECRET", "ADMIN_PASSWORD_HASH", "POSTGRES_PASSWORD", "LLM_API_KEY"]:
        assert values[key] == ""
    assert values["DATABASE_URL"] == ""
    assert values["ALEMBIC_DATABASE_URL"] == ""
    assert values["HEALTHCHECK_URL"] == ""
    assert not any(key.startswith("NEXT_PUBLIC_") and "SECRET" in key for key in values)


def test_env_files_are_ignored_but_templates_are_trackable():
    for name in [".env.dev", ".env.prod", "backend/.env", "apps/web/.env.local"]:
        assert (
            subprocess.run(
                ["git", "check-ignore", "--quiet", "--no-index", name], cwd=ROOT, check=False
            ).returncode
            == 0
        )
    for name in [".env.example", ".env.example.dev", ".env.example.prod"]:
        assert (
            subprocess.run(
                ["git", "check-ignore", "--quiet", "--no-index", name], cwd=ROOT, check=False
            ).returncode
            == 1
        )


@pytest.mark.parametrize("mode", ["dev", "prod"])
def test_compose_loads_selected_environment_and_rejects_missing_credentials(tmp_path, mode):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is required for config validation")
    if subprocess.run(
        ["docker", "compose", "version"], capture_output=True, check=False
    ).returncode:
        pytest.skip("Docker Compose CLI is required for config validation")

    (tmp_path / "docker").mkdir()
    compose_file = tmp_path / "docker" / f"docker-compose.{mode}.yml"
    shutil.copyfile(ROOT / "docker" / compose_file.name, compose_file)
    values = dict(dotenv_values(ROOT / f".env.example.{mode}"))
    values.update(
        POSTGRES_PASSWORD="test-only-$literal",
        DATABASE_URL="postgresql+asyncpg://test-only/unused_test",
        JWT_SECRET="test-only-signing-key-never-deploy",
        ADMIN_PASSWORD_HASH="$2b$04$test-only-placeholder-not-a-real-hash",
    )
    env_file = tmp_path / f".env.{mode}"
    # Do not inherit an operator's exported values over the isolated fixture.
    clean_env = {key: value for key, value in os.environ.items() if key not in values}

    def render():
        env_file.write_text("".join(f"{key}='{value}'\n" for key, value in values.items()))
        return subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(compose_file),
                "config",
                "--format",
                "json",
            ],
            check=False,
            env=clean_env,
            capture_output=True,
            text=True,
        )

    rendered = render()
    assert rendered.returncode == 0, "Compose could not render synthetic configuration"
    services = json.loads(rendered.stdout)["services"]
    # Compose serializes literal dollar signs as $$ for a reusable config document.
    for service in services.values():
        service["environment"] = {
            key: value.replace("$$", "$") for key, value in service.get("environment", {}).items()
        }
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"] == values["POSTGRES_PASSWORD"]
    # The engine runs from system cron; the stack must not bring back Celery/Redis.
    assert not {"redis", "worker", "beat", "flower"} & services.keys()
    api_env = services["api"]["environment"]
    assert api_env["DATABASE_URL"] == values["DATABASE_URL"]
    assert api_env["JWT_SECRET"] == values["JWT_SECRET"]
    assert api_env["ADMIN_PASSWORD_HASH"] == values["ADMIN_PASSWORD_HASH"]
    assert api_env["LLM_API_KEY"] == ""
    assert api_env["DEBUG"] == ("false" if mode == "prod" else "true")
    if mode == "dev":
        web_env = services["web"]["environment"]
        assert web_env["INTERNAL_API_URL"] == values["INTERNAL_API_URL"]
        assert web_env["NEXT_PUBLIC_SITE_URL"] == values["NEXT_PUBLIC_SITE_URL"]
        assert not {"JWT_SECRET", "ADMIN_PASSWORD_HASH", "POSTGRES_PASSWORD"} & web_env.keys()

    for required in ["POSTGRES_PASSWORD", "DATABASE_URL"] + (
        ["JWT_SECRET", "ADMIN_PASSWORD_HASH"] if mode == "prod" else []
    ):
        original = values[required]
        values[required] = ""
        assert render().returncode != 0, f"Compose must reject blank {required}"
        values[required] = original
