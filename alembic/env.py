import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Fix for Windows Cyrillic locale — psycopg2 reads system encoding otherwise
os.environ["PGCLIENTENCODING"] = "UTF8"

# Load app config & all models so Base.metadata is populated
from app.core.config import settings
from app.models.base import Base
import app.models  # noqa: F401 — registers all ORM models with Base.metadata

config = context.config

# Normalise to a sync psycopg2 URL regardless of input format
sync_url = settings.DATABASE_URL
sync_url = sync_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
sync_url = sync_url.replace("postgres://", "postgresql+psycopg2://", 1)
sync_url = sync_url.replace("postgresql://", "postgresql+psycopg2://", 1)
sync_url = sync_url.replace("?ssl=disable", "").replace("?ssl=require", "?sslmode=require")

config.set_main_option("sqlalchemy.url", sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
