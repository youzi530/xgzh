"""Alembic 环境.

特性:
1. URL 来源优先级: ``-x url=...`` > ``XGZH_TEST_DATABASE_URL`` > ``settings.database_url``
   - 让测试可临时切到独立 DB, 不污染开发库
2. 同步 / 异步双模式:
   - 走 ``postgresql+asyncpg`` 时使用 ``async_engine_from_config`` + ``run_sync``
   - 走 ``postgresql://`` (psycopg) 时按官方常规模式
3. ``include_schemas=False`` 避免误把 information_schema 当作 diff 对象
4. ``compare_type=True`` 让类型变更也走 autogen
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.base import Base

import app.db.models  # noqa: F401  ← 关键: 触发所有 model 注册到 Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_url() -> str:
    cli_args = context.get_x_argument(as_dictionary=True)
    if cli_args.get("url"):
        return cli_args["url"]
    if env_url := os.getenv("XGZH_TEST_DATABASE_URL"):
        return env_url
    return get_settings().database_url


target_metadata = Base.metadata


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """生成 SQL 脚本而不真正连库."""
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_online_async(url: str) -> None:
    cfg_section = config.get_section(config.config_ini_section) or {}
    cfg_section["sqlalchemy.url"] = url
    connectable = async_engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    url = _resolve_url()
    if url.startswith("postgresql+asyncpg") or url.startswith("sqlite+aiosqlite"):
        asyncio.run(_run_migrations_online_async(url))
        return
    cfg_section = config.get_section(config.config_ini_section) or {}
    cfg_section["sqlalchemy.url"] = url
    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _do_run_migrations(connection)
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
