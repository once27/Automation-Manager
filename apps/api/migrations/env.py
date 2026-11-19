"""Alembic migration environment for BrowserFleet API persistence."""

import os
from collections.abc import MutableMapping
from logging.config import fileConfig
from typing import Literal

from alembic import context
from browserfleet_api.db import models
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("BROWSERFLEET_DATABASE_URL")
if database_url is not None:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = models.Base.metadata


def include_name(
    name: str | None,
    type_: Literal[
        "schema",
        "table",
        "column",
        "index",
        "unique_constraint",
        "foreign_key_constraint",
    ],
    parent_names: MutableMapping[
        Literal["schema_name", "table_name", "schema_qualified_table_name"], str | None
    ],
) -> bool:
    """Exclude Alembic's own version table from model schema comparison."""
    return not (type_ == "table" and name == "alembic_version")


def run_migrations_offline() -> None:
    """Run migrations without an Engine, using literal SQL rendering."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations through a SQLAlchemy connection."""
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        context.configure(
            connection=supplied_connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table_schema=config.attributes.get("version_table_schema"),
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
