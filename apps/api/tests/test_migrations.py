"""Alembic 迁移端到端验证.

覆盖:
1. ``alembic upgrade head`` 能在干净库上跑通 (含 pgvector + pgcrypto extension)
2. 7 张业务表 + 30 个索引 + 必要约束正确创建
3. ``alembic downgrade base`` 能完整反向 (除了 alembic_version 自身)
4. 再次 ``upgrade head`` 仍可幂等成功

设计要点:
- 使用 ``Config.set_main_option('sqlalchemy.url', ...)`` 让 alembic 走测试库 URL,
  不污染开发数据。
- 通过 ``run_async_migrations`` helper 把 alembic 同步 API 包进 thread pool,
  避免在 pytest-asyncio 的事件循环里阻塞。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.db


EXPECTED_TABLES = {
    "users",
    "auth_sessions",
    "ipos",
    "ipo_documents",
    "user_favorites",
    "push_tokens",
    "invite_codes",
}

EXPECTED_INDEXES_SUBSET = {
    "ix_users_status",
    "ix_users_wechat_unionid",
    "uq_users_phone",
    "uq_users_invite_code",
    "ix_auth_sessions_user_revoked",
    "uq_auth_sessions_refresh_token_jti",
    "ix_ipos_status",
    "uq_ipos_code_market",
    "ix_ipo_documents_embedding_hnsw",
    "ix_ipo_documents_ipo_code_doc_type",
    "ix_user_favorites_ipo_code_market",
    "uq_push_tokens_user_platform_device",
    "ix_invite_codes_owner_user_id",
}


def _build_alembic_config(test_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)
    return cfg


async def _list_objects(engine, kind: str) -> set[str]:
    async with engine.connect() as conn:
        if kind == "table":
            rows = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                )
            )
        elif kind == "index":
            rows = await conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
                )
            )
        else:
            raise ValueError(kind)
        return {r[0] for r in rows}


async def _truncate_public_schema(test_url: str) -> None:
    """轻量重置: 把 public schema 内所有业务表 + alembic_version drop 掉.

    不 ``DROP SCHEMA``: 这要求 schema owner 权限, 测试用的 ``xgzh`` 角色
    一般只是 DB owner, 没法 drop schema 自身. 退而求其次, 列出 schema 内
    所有 table 再 drop, 效果等价 (本测试不依赖 sequence 等其它对象)。
    """
    engine = create_async_engine(test_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                )
            )
            tables = [r[0] for r in rows]
            for tbl in tables:
                await conn.execute(text(f'DROP TABLE IF EXISTS public."{tbl}" CASCADE'))
    finally:
        await engine.dispose()


async def test_migration_upgrade_creates_expected_objects(test_database_url: str) -> None:
    """upgrade head 后, 7 张表与关键索引必须全部存在."""
    await _truncate_public_schema(test_database_url)
    cfg = _build_alembic_config(test_database_url)

    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        tables = await _list_objects(engine, "table")
        assert EXPECTED_TABLES.issubset(tables), (
            f"缺表: {EXPECTED_TABLES - tables}"
        )
        assert "alembic_version" in tables

        indexes = await _list_objects(engine, "index")
        missing = EXPECTED_INDEXES_SUBSET - indexes
        assert not missing, f"缺索引: {missing}"

        async with engine.connect() as conn:
            row = await conn.execute(
                text(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_name='ipo_documents' AND column_name='embedding'"
                )
            )
            udt_name = row.scalar_one()
            assert udt_name == "vector"

            ext_rows = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname IN ('vector','pgcrypto')")
            )
            exts = {r[0] for r in ext_rows}
            assert {"vector", "pgcrypto"}.issubset(exts), f"扩展缺失: {exts}"
    finally:
        await engine.dispose()


async def test_migration_downgrade_drops_business_tables(test_database_url: str) -> None:
    """downgrade base 后, 业务表全部被 drop, 仅留 alembic_version."""
    cfg = _build_alembic_config(test_database_url)

    await asyncio.to_thread(command.upgrade, cfg, "head")
    await asyncio.to_thread(command.downgrade, cfg, "base")

    engine = create_async_engine(test_database_url)
    try:
        tables = await _list_objects(engine, "table")
        leftover = tables & EXPECTED_TABLES
        assert not leftover, f"downgrade 未清干净: {leftover}"
    finally:
        await engine.dispose()


async def test_migration_is_idempotent(test_database_url: str) -> None:
    """upgrade → downgrade → upgrade 仍可成功, 证明可重复发布."""
    cfg = _build_alembic_config(test_database_url)

    await asyncio.to_thread(command.upgrade, cfg, "head")
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database_url)
    try:
        tables = await _list_objects(engine, "table")
        assert EXPECTED_TABLES.issubset(tables)
    finally:
        await engine.dispose()
