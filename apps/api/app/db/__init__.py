"""数据库层（SQLAlchemy 2.0 async）.

公开 API:
    Base                  - 所有 ORM 模型的声明式基类
    get_engine()          - 获取 (lazy) async engine
    get_session_factory() - 获取 async_sessionmaker
    get_session()         - FastAPI Depends 用，async generator
    NAMING_CONVENTION     - 索引/约束统一命名规范
"""

from app.db.base import (
    NAMING_CONVENTION,
    Base,
    get_engine,
    get_session,
    get_session_factory,
)

__all__ = [
    "Base",
    "NAMING_CONVENTION",
    "get_engine",
    "get_session",
    "get_session_factory",
]
