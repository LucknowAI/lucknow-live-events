"""Alembic environment for the V1 engine — its own schema, its own version table.

Deliberate choices:

* **`version_table_schema = engine_v1`, `version_table = alembic_version_v1`.** The V0
  migration history is untouched and cannot collide. `DROP SCHEMA engine_v1 CASCADE`
  resets the engine including its migration history.
* **`include_object` filters to `engine_v1`.** With `include_schemas=True`, autogenerate
  would otherwise see every V0 table in `public` and cheerfully propose dropping it.
* **Sync driver.** The DSN's `+asyncpg` is rewritten to `psycopg2` here. Migrations are a
  short, serial, offline-ish operation; async Alembic buys nothing and costs clarity.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool, text

from alembic import context

# Make `v1.*` importable when alembic is invoked from `backend/`.
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from v1.models import SCHEMA, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

VERSION_TABLE = "alembic_version_v1"


def _database_url() -> str:
    url = os.getenv("V1_ALEMBIC_DATABASE_URL") or os.getenv("V1_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "V1_DATABASE_URL (or V1_ALEMBIC_DATABASE_URL) must be set to run V1 migrations"
        )
    return url.replace("+asyncpg", "+psycopg2").replace("postgresql://", "postgresql+psycopg2://")


def include_object(_object, name, type_, _reflected, compare_to) -> bool:
    """Only ever consider objects inside `engine_v1`."""
    if type_ == "table":
        schema = getattr(_object, "schema", None)
        return schema == SCHEMA
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        # The schema must exist before the version table can be created inside it.
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
            version_table_schema=SCHEMA,
            include_schemas=True,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
