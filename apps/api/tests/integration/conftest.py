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
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

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
from app.adapters.llm_client import ChatResult, TokenUsage
from app.adapters.sms import MockSMSAdapter, reset_sms_adapter, set_sms_adapter
from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.db.base import get_engine, get_session
from app.db.base import get_session_factory as _get_factory_lru
from app.main import create_app
from app.services.article_ingest.sources.base import ArticleRaw, ArticleSource

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
    """每条用例前清业务表 + 重置序列, 用例间数据完全隔离.

    与 alembic_version 解耦; 不 truncate alembic_version 防止把 schema 元数据
    清掉。``ipos`` 走 CASCADE 会顺带清 ``ipo_documents`` (FK CASCADE),
    但 ``ipo_documents`` 也支持 ``ipo_id IS NULL`` 的孤儿 chunk (BE-S2-003 后
    向 RAG 灌任意文档), 显式列出更稳。
    """
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE users, auth_sessions, user_favorites, ipos, "
                "ipo_documents, invite_codes, push_tokens, chat_sessions, "
                "articles, "  # article_topics 由 articles CASCADE 顺带清
                "brokers, conversion_events, "  # conversion_events 也走 brokers CASCADE, 显式列更稳
                "vip_orders, vip_memberships, "  # vip_memberships 走 vip_orders FK SET NULL, 不会 CASCADE 顺带清
                "feedbacks, "  # BE-S5-004: feedbacks.user_id FK SET NULL, 不会被 users CASCADE
                "invite_rewards, "  # BE-S5-005: invite_rewards.inviter_user_id FK CASCADE, users CASCADE 会清, 但显式列让用例间隔离更显
                "user_deletions, "  # BE-S5-003: user_deletions.user_id FK CASCADE, users CASCADE 会清, 显式列让用例间隔离
                "subscription_accounts, "  # BE-S6-001: user FK CASCADE, 显式列更稳
                "subscription_records, "  # BE-S6-001: account FK CASCADE 双重, 显式列更稳
                "knowledge_articles, "  # BE-S6-004: 知识库无 user FK, 必须显式 truncate
                "community_posts, "  # BE-S6-005: user FK CASCADE
                "community_comments, "  # post + user FK CASCADE
                "community_likes, "  # user FK CASCADE
                "community_reports, "  # user FK CASCADE
                "admin_audit_logs "  # BE-S11-E01: admin_user_id FK SET NULL, 显式列让用例间隔离
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
    import app.services.admin_audit_service as admin_audit_mod
    import app.services.agent.tools.historical as agent_historical_mod
    import app.services.agent.tools.hybrid_search as agent_hybrid_search_mod
    import app.services.agent.tools.peers as agent_peers_mod
    import app.services.article_ingest.dedup as article_dedup_mod
    import app.services.article_ingest.dispatcher as article_ingest_mod
    import app.services.article_ingest.sentiment_tagger as article_sentiment_mod
    import app.services.article_service as article_service_mod
    import app.services.article_tldr_service as article_tldr_mod
    import app.services.broker_service as broker_service_mod
    import app.services.conversion_service as conversion_service_mod
    import app.services.ipo_ingest_service as ingest_mod
    import app.services.ipo_service as ipo_service_mod
    import app.services.knowledge_service as knowledge_service_mod
    import app.services.payment.payment_service as payment_service_mod
    import app.services.user_deletion_service as user_deletion_mod
    import app.services.vip_service as vip_service_mod
    import scripts.backfill_historical_ipos as backfill_historical_mod
    import scripts.check_historical_coverage as check_historical_coverage_mod
    import scripts.seed_brokers as seed_brokers_mod

    # 多处都要 patch: 各 module 在 import 时把 ``get_session_factory`` 拷到自己
    # namespace, 改 ``app.db`` 不会影响 service module 的 local 引用. 漏 patch
    # 会导致 service 走真 DSN, 整条 e2e 看到空表. 用 setattr/getattr 字符串路径
    # 是因为 mypy 看不到 ``import xxx as alias`` 重新 export, 静态检查会报
    # ``attr-defined``; 测试代码本身就是要修这种 monkey-patch hack 行为, 直接
    # 走运行期反射.
    targets = [
        db_pkg,
        ingest_mod,
        ipo_service_mod,
        knowledge_service_mod,
        agent_peers_mod,
        agent_historical_mod,
        agent_hybrid_search_mod,
        article_ingest_mod,
        article_dedup_mod,
        article_sentiment_mod,
        article_tldr_mod,
        article_service_mod,
        broker_service_mod,
        conversion_service_mod,
        vip_service_mod,
        payment_service_mod,
        user_deletion_mod,
        admin_audit_mod,
        seed_brokers_mod,
        backfill_historical_mod,
        check_historical_coverage_mod,
    ]
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


# ─── 文章流水线 e2e 复用 fixtures (QA-S3-001) ─────────────────────────


class _StaticArticleSource:
    """静态 ``ArticleSource`` 实现, 给 ``mock_article_sources`` fixture 用.

    把构造时给定的 ``ArticleRaw`` 列表原样吐回; ``fetch`` 是 async 方法以
    匹配 ``ArticleSource`` 协议. 与 ``test_article_*_e2e.py`` 内联的私有
    实现等价, 抽到 conftest 复用 (QA-S3-001 / 后续 QA-S3-005 都要用).
    """

    def __init__(self, name: str, articles: list[ArticleRaw]) -> None:
        self.name = name
        self._articles = articles

    async def fetch(
        self, *, since: datetime | None = None
    ) -> list[ArticleRaw]:
        return list(self._articles)


def _make_article_raw(
    *,
    title: str,
    url: str,
    summary: str | None = None,
    source_name: str = "雪球",
    market: str = "HK",
    published_at: datetime | None = None,
    hot_score: float = 100.0,
) -> ArticleRaw:
    """``ArticleRaw`` 工厂; 默认字段适配文章流水线 e2e 场景.

    与 ``test_article_*_e2e.py`` 私有 helper 同口径; ``hot_score`` 走 Decimal
    避免 PG numeric 入库时丢精度.
    """
    return ArticleRaw(
        title=title,
        original_url=url,
        source_name=source_name,
        published_at=published_at or datetime.now(UTC),
        summary=summary,
        market=market,
        source_credibility=2,
        is_full_text_available=True,
        hot_score=Decimal(str(hot_score)),
    )


@pytest.fixture
def mock_article_sources() -> list[ArticleSource]:
    """5 篇覆盖 3 sentiment + 2 对 simhash near-duplicate 的固定文章池.

    设计 (QA-S3-001 §测试用例 1 金线 happy 锁定):

    - ``A1`` 腾讯利好  (bullish 用)
    - ``A2`` 腾讯中性  (neutral 用)
    - ``A3`` 腾讯利空  (bearish 用)
    - ``D1`` 港交所长文  (parent, 与 D2 1:1 转发关系)
    - ``D2`` 港交所长文  (D1 完全相同 title+summary, distance=0 必折叠)

    关键约束:
    1. ``A1/A2/A3/D1`` 标题主题各不相同, simhash 距离 >> 阈值 3 → 不会误折
    2. ``D1/D2`` 完全相同 title+summary → distance=0, 必折叠 (与
       ``test_article_dedup_e2e.py`` "严格转发" 同款保证)
    3. 全部命中 IPO 关键词 (腾讯控股 / 港交所), dispatcher 不丢条
    4. ``published_at`` 从早到晚: A1 < A2 < A3 < D1 < D2
       (D1 早于 D2 1 分钟, 保证 D1 = parent / D2 = child)

    返回 ``[ArticleSource]`` (单源即可); 测试侧再 monkeypatch
    ``dispatcher.register_sources`` 让 dispatcher 用这个源.
    """
    base = datetime.now(UTC) - timedelta(hours=1)
    # 注: 必须用 IPO 全名 "香港交易所" 而非短名 "港交所" — ``IPOKeywordIndex``
    # 走全字符串包含匹配, 不会自动 ``香港交易所 → 港交所`` 短化 (与 spec/03 §模块二
    # 关键词派生规则一致, 仅对 ``-W/-B/控股/集团`` 等后缀做去除).
    common_dup_title = (
        "香港交易所 Q3 IPO 募资额创新高 新股市场全面回暖 投行排队抢承销份额"
    )
    common_dup_summary = (
        "香港交易所发布最新统计, 第三季度新股募资额创近 5 年新高, 多只重磅 IPO "
        "上市首日涨幅可观, 多家投行加大 ECM 团队投入, 市场情绪持续回暖."
    )

    articles: list[ArticleRaw] = [
        _make_article_raw(
            title="腾讯控股 Q3 业绩超预期 净利润同比 +18% 派息提振股价",
            url="https://x.com/p/qa1-tx-bullish",
            summary=(
                "腾讯控股发布第三季度财报, 营收 +12% / 净利润 +18% 双双超出"
                "市场预期, 派息提振股价表现, 多家投行上调目标价."
            ),
            published_at=base + timedelta(minutes=0),
        ),
        _make_article_raw(
            title="腾讯控股召开股东周年大会 通过常规董事任命议案",
            url="https://x.com/p/qa1-tx-neutral",
            summary=(
                "腾讯控股于周三召开股东周年大会, 全部 12 项议案均获通过, "
                "包括董事任命 / 核数师续聘 / 股息派发等常规事项."
            ),
            published_at=base + timedelta(minutes=10),
        ),
        _make_article_raw(
            title="腾讯控股游戏业务遭欧盟反垄断调查 股价大跌 8%",
            url="https://x.com/p/qa1-tx-bearish",
            summary=(
                "欧盟委员会宣布对腾讯游戏业务在欧洲市场的份额展开反垄断调查, "
                "公司股价当日下跌 8%, 多家分析机构下调目标价."
            ),
            published_at=base + timedelta(minutes=20),
        ),
        _make_article_raw(
            title=common_dup_title,
            url="https://x.com/p/qa1-hkex-d1-original",
            summary=common_dup_summary,
            published_at=base + timedelta(minutes=30),  # D1 = parent (最早)
        ),
        _make_article_raw(
            title=common_dup_title,
            url="https://x.com/p/qa1-hkex-d2-repost",
            summary=common_dup_summary,
            published_at=base + timedelta(minutes=31),  # D2 = child
        ),
    ]
    return [_StaticArticleSource("雪球", articles)]


def _make_chat_result(content: str) -> ChatResult:
    """``ChatResult`` 简易工厂, 让 e2e 用例 mock LLM 返回固定 JSON."""
    return ChatResult(
        content=content,
        finish_reason="stop",
        usage=TokenUsage.empty(),
        model="zhipu/glm-4-flash",
        provider="zhipu",
        tool_calls=None,
    )


@pytest.fixture
def mock_sentiment_llm() -> Any:
    """sentiment_tagger 用的 mock chat: 按文章 title 关键词推断 sentiment.

    与 ``mock_article_sources`` 配套: A1 命中 "超预期/+18%/上调" → bullish,
    A3 命中 "反垄断/下跌/下调" → bearish, 其余 → neutral. ``call_log`` 暴露
    给测试侧断言调用次数 (TLDR 缓存 / sentiment fallback 都要查).

    返回 callable, 测试侧 ``monkeypatch.setattr(sentiment_tagger, "chat", ...)``.
    """

    call_log: dict[str, int] = {"count": 0}

    bullish_kw = ("超预期", "增长", "上调", "派息", "回暖", "+", "新高", "看多")
    bearish_kw = ("反垄断", "调查", "下跌", "下调", "处罚", "退市", "风险", "看空")

    async def fake_chat(**kwargs: Any) -> ChatResult:
        call_log["count"] += 1
        messages = kwargs.get("messages", [])
        user_msg = next((m for m in messages if m["role"] == "user"), None)
        body = user_msg["content"].split("\n\n", 1)[1] if user_msg else "[]"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = []

        articles_out: list[dict[str, Any]] = []
        for item in payload:
            text_blob = (item.get("title") or "") + " " + (item.get("summary") or "")
            if any(k in text_blob for k in bearish_kw):
                sentiment, score = "bearish", -0.7
            elif any(k in text_blob for k in bullish_kw):
                sentiment, score = "bullish", 0.8
            else:
                sentiment, score = "neutral", 0.0
            articles_out.append(
                {
                    "id": item["id"],
                    "sentiment": sentiment,
                    "score": score,
                    "keywords": ["腾讯", "财报"]
                    if "腾讯" in text_blob
                    else ["港交所", "IPO"],
                }
            )
        content = json.dumps({"articles": articles_out}, ensure_ascii=False)
        return _make_chat_result(content)

    fake_chat.call_log = call_log  # type: ignore[attr-defined]
    return fake_chat
