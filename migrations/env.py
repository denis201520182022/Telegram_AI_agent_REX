import asyncio
from logging.config import fileConfig
import sys
from os.path import abspath, dirname

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ------------------ НАЧАЛО НАСТРОЙКИ ПУТЕЙ И ИМПОРТОВ ------------------
# 1. Добавляем корневую папку проекта в sys.path, чтобы видеть папку src
sys.path.insert(0, dirname(dirname(abspath(__file__))))

# 2. Теперь можем импортировать наши настройки и модели
from src.config import settings
from src.database.models import Base
# ------------------ КОНЕЦ НАСТРОЙКИ ------------------

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ------------------ ПОДМЕНА URL И МЕТАДАННЫХ ------------------
# 3. Подменяем sqlalchemy.url на тот, что в нашем .env файле
import os

# Пытаемся взять URL из переменной окружения, если её нет - берем из конфига
cmd_line_url = os.environ.get("DATABASE_URL")
if cmd_line_url:
    config.set_main_option("sqlalchemy.url", cmd_line_url)
else:
    # Если мы идем через pgbouncer, для миграций подменяем его на прямой адрес базы
    target_url = settings.DB_URL.replace("pgbouncer", "postgres_db")
    config.set_main_option("sqlalchemy.url", target_url)

# 4. Указываем метаданные моделей для автогенерации миграций
target_metadata = Base.metadata
# ------------------ КОНЕЦ ПОДМЕНЫ ------------------


# ------------------ КОНЕЦ ИЗМЕНЕНИЙ ------------------

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, 
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
