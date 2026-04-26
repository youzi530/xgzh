"""集成测试共享 fixtures (QA-001).

定位:
- 与 ``tests/`` 根目录的"单功能"测试 (例如 ``test_favorites.py``, ``test_ipos_list.py``)
  并行存在; 那些测试现在还把同样的 fixtures 内联在自己文件里, 后续可以分阶段
  迁移过来 (本 PR 不动它们以保持 diff 最小)。
- 这里的 fixtures 设计为 *组合式*: ``client`` fixture 把 PG schema / 内存 Redis /
  mock SMS / session factory 一次性串起来, 让 e2e 用例的 setup 只剩 1 行。

关键设计:
- ``test_database_url`` 来自顶层 ``tests/conftest.py``: 没设 ``XGZH_TEST_DATABASE_URL``
  环境变量就 skip 整个 db 测试 session, CI / 本地都用同一道 gate.
- 整个 module 内的用例默认带 ``pytest.mark.db``: 不需要每个文件 ``pytestmark``
  自己写 (在 module-level fixture 里要求 ``schema_at_head``, 因此自动需要 DB)。
- ``patch_session_factory`` 是给 ``ipo_ingest_service`` / ``ipo_service`` 用的:
  这两个服务直接 ``get_session_factory()`` 拿 factory, 而不是依赖注入; 必须
  monkey-patch module-level cache 才能让它们用测试库.
- ``fake_llm`` (本文件): 替换 ``llm_client.stream_chat``, 让 e2e 不依赖真 LLM key
  也不会因本地 ``.env`` 偶然有 key 而打远程请求 (CI 干净 + 本地确定性)。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.adapters.sms import MockSMSAdapter, reset_sms_adapter, set_sms_adapter
from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.db.base import get_engine, get_session
from app.db.base import get_session_factory as _get_factory_lru
from app.main import create_app

# integration 包内所有用例都需要真 PG; 没配 ``XGZH_TEST_DATABASE_URL`` 时
# 顶层 ``tests/conftest.py`` 已经会 skip, 这里再用 ``pytestmark`` 给 IDE / pytest
# 输出看更清楚.
pytestmark = pytest.mark.db


# ─── Alembic schema 准备 ─────────────────────────────────────────


def _build_alembic_config(test_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)
    return cfg


async def _drop_business_tables(url: str) -> None:
    """把测试 DB ``public`` schema 下所有表 DROP CASCADE.

    包括 ``alembic_version`` 表 — 这样下一次 ``alembic upgrade head`` 会
    重新跑全部迁移, 测试库永远是干净的最终态.
    """
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
            for (tbl,) in rows:
                await conn.execute(text(f'DROP TABLE IF EXISTS public."{tbl}" CASCADE'))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
async def schema_at_head(test_database_url: str) -> AsyncIterator[str]:
    """每个 module 启动时把测试库重置到最新 schema.

    用 ``module`` 而不是 ``function`` 是为了避免每条用例都跑一次 alembic
    (e2e 用例少, 模块级别 reset + 用例级别 ``truncate_all`` 已足够隔离)。
    """
    await _drop_business_tables(test_database_url)
    cfg = _build_alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield test_database_url


@pytest.fixture
async def db_engine(schema_at_head: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(schema_at_head, future=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def truncate_all(db_engine: AsyncEngine) -> AsyncIterator[None]:
    """每条用例前清 7 张业务表 + 重置序列, 用例间数据完全隔离.

    与 alembic_version 解耦; 不 truncate alembic_version 防止把 schema 元数据
    清掉。
    """
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE users, auth_sessions, user_favorites, ipos, "
                "invite_codes, push_tokens, chat_sessions "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


# ─── Adapters / 外部依赖 mock ─────────────────────────────────────


@pytest.fixture
async def redis_client() -> AsyncIterator[InMemoryRedisClient]:
    """整个用例期间用 InMemoryRedisClient 替代真 Redis.

    覆盖 INCR / EXPIRE / Lua 脚本 / TTL, 与 RealRedisClient 行为一致 (BE-005);
    避免 e2e 依赖外部 Redis 实例。
    """
    client = InMemoryRedisClient()
    set_redis_client(client)
    yield client
    await client.aclose()
    reset_redis_client()


@pytest.fixture
async def mock_sms() -> AsyncIterator[MockSMSAdapter]:
    """OTP 走 MockSMSAdapter; 不真发短信, 测试可以读到投递的 ``code``.

    e2e 用例不直接读它 (改为提前用 ``otp_service.store_otp`` 埋), 但仍把
    adapter 注入进去, 防止默认 Aliyun 占位 adapter 被意外触发。
    """
    adapter = MockSMSAdapter()
    set_sms_adapter(adapter)
    yield adapter
    reset_sms_adapter()


@pytest.fixture
async def patch_session_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """让模块级 ``get_session_factory()`` 调用拿到测试 factory.

    必要原因: ``ipo_ingest_service`` / ``ipo_service`` 内部直接调用 module-level
    ``get_session_factory()`` (不走 FastAPI Depends), 默认会拿生产 DSN.
    本 fixture 把 lru_cache 清掉 + 替换 module attribute, yield 期间所有 service
    层调用都拉到测试库; 退出时还原。
    """
    _get_factory_lru.cache_clear()
    get_engine.cache_clear()

    import app.db as db_pkg
    import app.services.ipo_ingest_service as ingest_mod
    import app.services.ipo_service as ipo_service_mod

    # 三处都要 patch: 各 module 在 import 时把 ``get_session_factory`` 拷到自己
    # namespace, 改 ``app.db`` 不会影响 service module 的 local 引用. 漏 patch
    # 会导致 service 走真 DSN, 整条 e2e 看到空表. 用 setattr/getattr 字符串路径
    # 是因为 mypy 看不到 ``import xxx as alias`` 重新 export, 静态检查会报
    # ``attr-defined``; 测试代码本身就是要修这种 monkey-patch hack 行为, 直接
    # 走运行期反射.
    targets = [db_pkg, ingest_mod, ipo_service_mod]
    originals: list[object] = [
        getattr(mod, "get_session_factory") for mod in targets  # noqa: B009
    ]
    for mod in targets:
        setattr(mod, "get_session_factory", lambda: session_factory)  # noqa: B010
    try:
        yield
    finally:
        for mod, orig in zip(targets, originals, strict=True):
            setattr(mod, "get_session_factory", orig)  # noqa: B010 - dynamic restore
        _get_factory_lru.cache_clear()
        get_engine.cache_clear()


@pytest.fixture
async def fake_llm(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[str]]:
    """把 ``llm_client.stream_chat`` 替换为可预测的固定 token 序列 + 免责声明.

    - 解耦 e2e 测试与真 LLM 服务: CI 不需要 LLM Key, 本地有 Key 也不会被偷打.
    - fake 末尾显式 yield 真 ``DISCLAIMER`` 字符串, 保证端到端协议"SSE 流末尾必含
      合规免责声明"在测试里能被验证.
    - 返回 yield 的 token list, 测试可以用来断言 SSE 透传无丢失.
    """
    from app.adapters import llm_client

    fake_tokens = [
        "**基本面摘要**\n",
        "本股票 PE 适中, 行业空间大. ",
        "募资规模合理.\n\n",
        "**核心风险点 Top 3**\n",
        "1. 估值偏高\n2. 行业波动\n3. 募投项目落地不确定\n",
    ]

    async def _fake_stream_chat(messages, **kwargs):  # type: ignore[no-untyped-def]
        for tok in fake_tokens:
            yield tok
        # 复刻真 stream_chat 末尾的 disclaimer 追加逻辑
        full = "".join(fake_tokens)
        if "不构成投资建议" not in full:
            yield llm_client.DISCLAIMER

    monkeypatch.setattr(llm_client, "stream_chat", _fake_stream_chat)
    yield fake_tokens


# ─── 复合 fixture: 一行起 e2e 客户端 ────────────────────────────────


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    redis_client: InMemoryRedisClient,  # noqa: ARG001
    mock_sms: MockSMSAdapter,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    fake_llm: list[str],  # noqa: ARG001
) -> AsyncIterator[httpx.AsyncClient]:
    """一站式 ASGI 客户端: schema 已升头 + 数据已清 + Redis/SMS/LLM mock 已就位.

    用法:
        async def test_xx(client: httpx.AsyncClient): ...

    底层走 ``httpx.ASGITransport``: 不开 socket, 测试运行速度 ~50 个 case / 秒;
    适合本地快速反馈和 CI 单一 worker。
    """
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
