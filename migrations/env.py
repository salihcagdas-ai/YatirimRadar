"""Alembic configuration for the local PostGIS database and application models."""

from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers

from yatirimradar.config import get_settings
from yatirimradar.store.db import get_engine
from yatirimradar.store.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use the database URL from the application settings.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def _include_object(
    object_: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Limit generated migrations to application tables.

    PostGIS also installs tables for topology and spatial reference systems.
    Skip reflected tables outside the ORM metadata to preserve these extensions.
    """
    if type_ == "table" and reflected and name not in target_metadata.tables:
        return False
    return bool(
        alembic_helpers.include_object(object_, name, type_, reflected, compare_to)
    )


def run_migrations_offline() -> None:
    """Generate migration SQL without opening a database connection."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=alembic_helpers.render_item,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations to the configured database."""
    connectable = get_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            process_revision_directives=alembic_helpers.writer,
            render_item=alembic_helpers.render_item,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
