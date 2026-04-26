"""项目共享测试 fixture.

约定:
- 任何标记为 ``@pytest.mark.db`` 的用例需要真 Postgres,
  未配置 ``XGZH_TEST_DATABASE_URL`` 时自动 skip, 不会让 CI 红。
- 测试数据库与开发库必须不同 (URL 末段必须是 ``xgzh_test`` 或包含 ``test``),
  防止误清开发数据。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """返回测试 DB URL；未配置则 skip 整个 session 内的 db 测试."""
    url = os.getenv("XGZH_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "需要 Postgres 测试库. 设置 XGZH_TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host:port/db_test 后重跑.",
            allow_module_level=False,
        )
    if "test" not in url.rsplit("/", 1)[-1]:
        pytest.fail(
            "XGZH_TEST_DATABASE_URL 末段数据库名必须包含 'test', 防止误清开发数据"
        )
    return url


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """没有 XGZH_TEST_DATABASE_URL 时, 自动给 ``db`` marker 用例打上 skip."""
    if os.getenv("XGZH_TEST_DATABASE_URL"):
        return
    skip_db = pytest.mark.skip(reason="未设置 XGZH_TEST_DATABASE_URL, 跳过 DB 集成测试")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip_db)


@pytest.fixture(autouse=True)
def _isolate_get_settings_cache() -> Iterator[None]:
    """每条用例后清空 get_settings 缓存, 防止环境变量串扰."""
    yield
    from app.core.config import get_settings

    get_settings.cache_clear()
