"""SQLAlchemy 2.0 async 基础设施.

设计要点：
1. 强制走 async (asyncpg). 同步连接禁用，避免误用阻塞主事件循环。
2. 全局唯一 ORM 基类 ``Base``；统一命名规范 (NAMING_CONVENTION) 写在
   ``MetaData`` 上, 这样 alembic autogenerate 会自动生成 ix_/uq_/fk_/pk_
   规范的对象名，避免和数据库 introspection 时出现幽灵 diff。
3. ``get_engine`` / ``get_session_factory`` 走 ``functools.lru_cache``,
   保证整个进程只创建一份 engine（对 asyncpg 连接池友好）。
4. ``get_session`` 是 FastAPI Depends 的 contract: ``async generator``,
   异常时自动 rollback, 正常退出时自动 commit。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有业务 model 继承自这里."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo_sql,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI Depends 用的 session 生成器.

    规则:
    - 业务路径正常返回时不 commit (由 service 层显式 commit, 控制事务边界)
    - 业务异常时自动 rollback, 防止事务遗留
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
