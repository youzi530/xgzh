"""QA-S3-001: 文章流水线 e2e 集成测.

定位
====
覆盖 BE-S3-002 ~ 006 全链路 — 同一个文件内串通走完
``ingest → 去重 → 情感打标 → TL;DR 缓存 → 列表 / 详情 / 搜索``,
而非各 stage 单独验证 (那些已经分别在 ``test_article_*_e2e.py`` 里覆盖).

为什么再开一个文件
==================
- ``test_article_ingest_e2e.py`` 验 dispatcher 单链
- ``test_article_dedup_e2e.py`` 验 simhash 折叠
- ``test_article_sentiment_e2e.py`` 验 LLM 打标
- ``test_article_tldr_api.py`` 验 TLDR 缓存
- ``test_article_api.py`` 验列表 / 详情 / 搜索

但 spec/10 §QA-S3-001 锁定 6 条 *端到端* 用例: 必须验证 "1 次 ingest 之后,
GET /articles 看到的是去重 + 打标 + 折叠 后的最终态" — 即各阶段串行起来不会
互相打架. 这种 cross-stage assertion 没法在单 stage 文件里写.

测试用例 (与 spec/10 §QA-S3-001 一一对齐)
=========================================
1.  ``test_pipeline_happy_full_chain`` — mock 5 篇 → dispatcher → 5 inserted +
    1 fold + 5 tagged → GET /articles 返回 4 行 (折叠后 parent + 1 child 隐藏)
2.  ``test_tldr_cache_hit_after_pipeline`` — 跑完流水线 → POST /tldr 200 + ok →
    二次调用走 Redis 缓存 (TLDR LLM 不再被打)
3.  ``test_tldr_insufficient_data_with_one_article`` — 1 篇 → POST /tldr →
    status='insufficient_data', LLM 不被调用
4.  ``test_search_chinese_english_mixed`` — 5 篇全含 ``美团`` (其中 2 篇标题混
    ``Meituan``) → search?q=美团 命中 5 行
5.  ``test_sentiment_fallback_on_llm_invalid_json`` — LLM 返回非 JSON →
    sentiment_tagger 走 neutral 兜底, 全 5 篇都拿到 sentiment='neutral' /
    score=0.0; dispatcher 主流程 errors=0
6.  ``test_dedup_threshold_boundary`` — 2 篇完全同主题 (folded) + 1 篇完全异
    主题 (not folded) → GET /articles 返回 2 行 parent

依赖
====
- BE-S3-002 (dispatcher)
- BE-S3-003 (simhash dedup)
- BE-S3-004 (sentiment_tagger)
- BE-S3-005 (article_tldr_service)
- BE-S3-006 (article_service list / detail / search)

不验
====
- 雪球 / 智通 真实 HTTP (各 source 单测覆盖)
- LLM 真实调用 (单测 + sentiment_tagger 内部容错)
- BE 内部 cache 实现 (走 Redis InMemory mock; cache key 命中由用例数验证)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.llm_client import ChatResult, TokenUsage
from app.db.models import IPO, Article, ArticleTopic
from app.services import article_tldr_service as tldr_mod
from app.services.article_ingest import dispatcher
from app.services.article_ingest import sentiment_tagger as st
from app.services.article_ingest.sources.base import ArticleRaw, ArticleSource

pytestmark = pytest.mark.db


# ─── 共用 helpers (本文件内部, 与 conftest 复用 fixture 互补) ───────────────


async def _seed_pipeline_ipos(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """种 ``mock_article_sources`` 配套的活跃 IPO.

    对齐 fixture 文章主题: 腾讯控股 + 港交所 + 美团点评 (中英混搜索用)
    + 一个 不被任何文章命中的 IPO 占位 (验证关键词索引不误命中).
    """
    async with session_factory() as s:
        s.add_all(
            [
                IPO(
                    code="00700.HK",
                    name="腾讯控股",
                    market="HK",
                    status="upcoming",
                    listing_date=datetime(2026, 5, 1).date(),
                    data_source="seed-qa-s3-001",
                ),
                IPO(
                    code="00388.HK",
                    name="香港交易所",
                    market="HK",
                    status="upcoming",
                    listing_date=datetime(2026, 5, 15).date(),
                    data_source="seed-qa-s3-001",
                ),
                IPO(
                    code="03690.HK",
                    name="美团点评",
                    market="HK",
                    status="upcoming",
                    listing_date=datetime(2026, 6, 1).date(),
                    data_source="seed-qa-s3-001",
                ),
            ]
        )
        await s.commit()


def _patch_dispatcher_register_sources(
    monkeypatch: pytest.MonkeyPatch, sources: list[ArticleSource]
) -> None:
    """让 ``dispatcher.register_sources`` 返回我们的假 sources."""
    monkeypatch.setattr(dispatcher, "register_sources", lambda **kw: sources)


def _patch_dispatcher_invalidate(monkeypatch: pytest.MonkeyPatch) -> None:
    """关掉 cache invalidate, 简化 assertion (Redis InMemory 能正常清, 但
    本文件大部分 case 不验缓存计数, 关掉避免噪音). 仅 TLDR 缓存命中用例例外."""

    async def _noop(*ns: str) -> int:
        return 0

    monkeypatch.setattr(dispatcher, "invalidate_namespace", _noop)


def _make_chat_result(content: str) -> ChatResult:
    return ChatResult(
        content=content,
        finish_reason="stop",
        usage=TokenUsage.empty(),
        model="zhipu/glm-4-flash",
        provider="zhipu",
        tool_calls=None,
    )


# ─── 1. 金线 happy: ingest → dedup → tag → list 串行 ────────────────────


async def test_pipeline_happy_full_chain(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    mock_article_sources: list[ArticleSource],
    mock_sentiment_llm: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全链路金线:

    - dispatcher 抓 5 篇 → 全部命中 IPO 关键词 → 5 行入库
    - simhash 给 5 篇全填 → D1/D2 distance=0 → 1 个 article_topics 行
    - sentiment_tagger 给 5 篇打标 (mock 按关键词推断 sentiment)
    - GET /articles → 返回 4 行 (D2 child 折叠不出现)
    - 验 ``A1=bullish / A2=neutral / A3=bearish / D1=parent`` 正确归类
    """
    await _seed_pipeline_ipos(session_factory)

    _patch_dispatcher_register_sources(monkeypatch, mock_article_sources)
    _patch_dispatcher_invalidate(monkeypatch)
    monkeypatch.setattr(st, "chat", mock_sentiment_llm)

    stats = await dispatcher.run_ingest_articles_job()

    # ─── stage 1+2: dispatcher + dedup ─────────────────────────────
    assert stats["sources"] == 1
    assert stats["fetched"] == 5
    assert stats["matched"] == 5, "5 篇全部命中 IPO 关键词"
    assert stats["inserted"] == 5
    assert stats["skipped"] == 0
    assert stats["simhash_filled"] == 5
    assert stats["topics_linked"] == 1, (
        f"D1/D2 严格转发 → 1 个 topic, 实际 {stats['topics_linked']}"
    )

    # ─── stage 3: sentiment_tagger ─────────────────────────────────
    assert stats["sentiment_tagged"] == 5
    assert stats["errors"] == 0
    assert mock_sentiment_llm.call_log["count"] >= 1, "LLM 至少被调一次"

    # ─── stage 4: 验 DB 状态 ───────────────────────────────────────
    async with session_factory() as s:
        rows = (
            await s.execute(
                select(
                    Article.original_url,
                    Article.sentiment,
                    Article.sentiment_score,
                ).order_by(Article.published_at)
            )
        ).all()
    assert len(rows) == 5

    by_url = {url: (sent, score) for url, sent, score in rows}
    bullish_url = "https://x.com/p/qa1-tx-bullish"
    neutral_url = "https://x.com/p/qa1-tx-neutral"
    bearish_url = "https://x.com/p/qa1-tx-bearish"
    assert by_url[bullish_url][0] == "bullish"
    assert by_url[bullish_url][1] is not None and by_url[bullish_url][1] > 0
    assert by_url[neutral_url][0] == "neutral"
    assert by_url[bearish_url][0] == "bearish"
    assert by_url[bearish_url][1] is not None and by_url[bearish_url][1] < 0

    # ─── stage 5: GET /articles 看到 4 行 (D2 child 隐藏) ──────────
    resp = await client.get("/api/v1/articles", params={"size": 50})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 4, (
        f"折叠后应剩 4 行 (3 篇腾讯独立 + 1 篇港交所 parent), 实际 total={data['total']}"
    )
    returned_urls = {it["original_url"] for it in data["items"]}
    assert "https://x.com/p/qa1-hkex-d2-repost" not in returned_urls, (
        "D2 是 child, 不应出现在列表"
    )
    assert "https://x.com/p/qa1-hkex-d1-original" in returned_urls


# ─── 2. TLDR 缓存命中: 二次调用 LLM 不再被打 ───────────────────────────


async def test_tldr_cache_hit_after_pipeline(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5 篇 IPO=00700.HK 文章 → POST /tldr 一次 (LLM 调) → 二次调用走缓存
    (LLM call_log 不增).

    本用例不走 dispatcher 流水线 (TLDR 缓存与 ingest 解耦), 直接 INSERT 入库
    并人工标 sentiment, 节省一次完整 ingest 的开销 + 让 LLM call_log 计数干净.
    """
    related = [{"code": "00700.HK", "market": "HK", "name": "腾讯控股"}]
    base = datetime.now(UTC)
    async with session_factory() as s:
        for i in range(5):
            s.add(
                Article(
                    title=f"腾讯控股 Q3 业绩亮点 #{i}",
                    summary=(
                        "腾讯控股发布第三季度财报, 营收同比增长稳健, "
                        f"派息提振股价表现 — 第 {i} 段."
                    ),
                    source_name="雪球",
                    source_credibility=2,
                    original_url=f"https://x.com/p/qa1-tldr-cache-{i}",
                    market="HK",
                    related_ipos=related,
                    sentiment="bullish",
                    sentiment_score=Decimal("0.700"),
                    keywords=["腾讯", "财报"],
                    hot_score=Decimal(100),
                    is_full_text_available=True,
                    published_at=base - timedelta(minutes=i),
                )
            )
        await s.commit()

    call_log: dict[str, int] = {"count": 0}

    async def fake_chat(**kwargs: Any) -> ChatResult:
        call_log["count"] += 1
        out = {
            "bullish_ratio": 0.6,
            "neutral_ratio": 0.3,
            "bearish_ratio": 0.1,
            "bullish_points": ["营收稳健", "派息提振"],
            "bearish_points": ["监管风险"],
            "source_article_ids": [],
        }
        import json

        return _make_chat_result(json.dumps(out, ensure_ascii=False))

    monkeypatch.setattr(tldr_mod, "chat", fake_chat)

    # 第一次: LLM 必被调
    r1 = await client.post(
        "/api/v1/articles/tldr",
        json={"scope": "ipo", "scope_value": "00700.HK"},
    )
    assert r1.status_code == 200, r1.text
    p1 = r1.json()
    assert p1["status"] == "ok"
    assert p1["article_count"] == 5
    assert call_log["count"] == 1

    # 第二次: 缓存命中, LLM 不再被调
    r2 = await client.post(
        "/api/v1/articles/tldr",
        json={"scope": "ipo", "scope_value": "00700.HK"},
    )
    assert r2.status_code == 200, r2.text
    p2 = r2.json()
    assert p2["status"] == "ok"
    assert call_log["count"] == 1, (
        f"缓存应命中, LLM 不应被二次调用, 实际 count={call_log['count']}"
    )
    # 缓存内容一致 (generated_at 也是缓存的, 走 Redis fixture 不会变)
    assert p1["generated_at"] == p2["generated_at"]


# ─── 3. 不足数据兜底: 1 篇 → insufficient_data ─────────────────────────


async def test_tldr_insufficient_data_with_one_article(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候选池 < ``_MIN_ARTICLES_FOR_LLM`` (3) → 直接返兜底, LLM 不被调."""
    related = [{"code": "00700.HK", "market": "HK", "name": "腾讯控股"}]
    async with session_factory() as s:
        s.add(
            Article(
                title="腾讯控股孤儿文章",
                summary="只有这一篇, 池子不够 LLM 调用",
                source_name="雪球",
                source_credibility=2,
                original_url="https://x.com/p/qa1-tldr-orphan",
                market="HK",
                related_ipos=related,
                sentiment="neutral",
                sentiment_score=Decimal("0.000"),
                keywords=[],
                hot_score=Decimal(100),
                is_full_text_available=True,
                published_at=datetime.now(UTC),
            )
        )
        await s.commit()

    call_log: dict[str, int] = {"count": 0}

    async def fake_chat(**kwargs: Any) -> ChatResult:
        call_log["count"] += 1
        return _make_chat_result("{}")

    monkeypatch.setattr(tldr_mod, "chat", fake_chat)

    resp = await client.post(
        "/api/v1/articles/tldr",
        json={"scope": "ipo", "scope_value": "00700.HK"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "insufficient_data"
    assert data["article_count"] == 1
    assert call_log["count"] == 0, "池 < 3 时 LLM 不应被调用"
    assert "不足" in data["message"]
    # 不足兜底也带免责声明 (端层 ensure_disclaimer 兜)
    assert "不构成投资建议" in data["message"]


# ─── 4. 全文搜索中英文混合: q=美团 命中 5 行 ───────────────────────────


async def test_search_chinese_english_mixed(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """corpus 里 5 篇都含 ``美团`` (其中 2 篇 title 混 ``Meituan`` 英文转写),
    search?q=美团 命中 5 行.

    验证点:
    1. CJK 字符级预切 (alembic 0005 + ``_cjk_presplit``) 正确生效
    2. 中英混内容不破坏 tsvector 切分 (英文 token 不污染中文匹配)
    3. ``size=20`` 默认值能装下 5 行
    """
    related = [{"code": "03690.HK", "market": "HK", "name": "美团点评"}]
    titles = [
        "美团 Q3 外卖订单创新高",
        "美团到店酒旅业务持续增长",
        "美团闪购下沉市场发力",
        # 中英混 (英文转写并存)
        "美团 (Meituan) Q3 financial results beat estimates",
        "Meituan 美团点评新一轮港股回购计划落地",
    ]
    async with session_factory() as s:
        for i, title in enumerate(titles):
            s.add(
                Article(
                    title=title,
                    summary=(
                        "美团点评今日披露最新经营数据, "
                        "Meituan delivery and lifestyle services revenue grew steadily."
                    ),
                    source_name="雪球",
                    source_credibility=2,
                    original_url=f"https://x.com/p/qa1-mt-{i}",
                    market="HK",
                    related_ipos=related,
                    sentiment="bullish",
                    sentiment_score=Decimal("0.600"),
                    keywords=["美团", "外卖"],
                    hot_score=Decimal(100),
                    is_full_text_available=True,
                    published_at=datetime.now(UTC) - timedelta(minutes=i),
                )
            )
        await s.commit()

    resp = await client.get("/api/v1/search/articles", params={"q": "美团"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 5, (
        f"q=美团 应命中 5 行 (含中英混), 实际 total={data['total']}; "
        f"items={[it['title'] for it in data['items']]}"
    )
    assert data["query"] == "美团"
    returned_titles = {it["title"] for it in data["items"]}
    for expected in titles:
        assert expected in returned_titles, (
            f"标题 {expected!r} 未被检索到 (CJK 切分 / 中英混兼容回归)"
        )


# ─── 5. 情感打标失败兜底: LLM 非 JSON → neutral / 0.0 ───────────────────


async def test_sentiment_fallback_on_llm_invalid_json(
    client: httpx.AsyncClient,  # noqa: ARG001 — 仅起 fixture 链
    session_factory: async_sessionmaker[AsyncSession],
    mock_article_sources: list[ArticleSource],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 整批 + 单条 fallback 都返非 JSON → ``_tag_batch`` 单条降级也失败 →
    全部走 ``_tag_one_with_fallback`` neutral 兜底:

    - sentiment='neutral' / score=0.000 / keywords=[]
    - dispatcher 主流程 ``stats.errors=0`` (打标失败不破坏 ingest)
    - ``stats.sentiment_tagged`` 仍 == 5 (兜底也算成功打标)

    BE-S3-004 §设计要点 锁定的"永不抛, 永不阻塞主流程"行为, 必须 e2e 守住.
    """
    await _seed_pipeline_ipos(session_factory)

    _patch_dispatcher_register_sources(monkeypatch, mock_article_sources)
    _patch_dispatcher_invalidate(monkeypatch)

    invalid_call_log: dict[str, int] = {"count": 0}

    async def fake_invalid_chat(**kwargs: Any) -> ChatResult:
        invalid_call_log["count"] += 1
        return _make_chat_result("not a valid json {{{ }}}")

    monkeypatch.setattr(st, "chat", fake_invalid_chat)

    stats = await dispatcher.run_ingest_articles_job()

    assert stats["inserted"] == 5
    assert stats["errors"] == 0, "打标失败不应让 dispatcher errors > 0"
    assert stats["sentiment_tagged"] == 5, (
        f"neutral fallback 也算 tagged, 实际 {stats['sentiment_tagged']}"
    )
    # 单条 fallback 至少调一次, 整批 + 5 单条 = 至少 6 次 (会因 batch_size 略浮动)
    assert invalid_call_log["count"] >= 2, (
        f"应触发 batch + 单条 fallback 多次调用, 实际 {invalid_call_log['count']}"
    )

    async with session_factory() as s:
        rows = (
            await s.execute(
                select(Article.sentiment, Article.sentiment_score, Article.keywords)
            )
        ).all()

    assert len(rows) == 5
    for sentiment, score, keywords in rows:
        assert sentiment == "neutral", (
            f"LLM 全失败时所有文章应 neutral, 实际 {sentiment}"
        )
        assert score == Decimal("0.000")
        assert keywords == []


# ─── 6. 去重 + 排序边界: 折叠 vs 不折叠 ────────────────────────────────


async def test_dedup_threshold_boundary(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    mock_sentiment_llm: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """构造 3 篇 ``ArticleRaw``:

    - ``X1, X2``: title+summary 完全相同 → simhash distance=0, 必折叠
      (与 dedup_e2e §"严格转发"同款保证, 单字差距离波动 4-9 不可靠)
    - ``Y``: 完全不同主题 → distance >> 阈值 3, 不折叠

    流水线跑完后:
    - 入库 3 行
    - 1 个 article_topics (X1=parent, X2=child)
    - GET /articles 返回 2 行 (X1 + Y; X2 折叠不出现)
    """
    await _seed_pipeline_ipos(session_factory)

    base = datetime.now(UTC) - timedelta(hours=1)

    common_title = (
        "腾讯控股 Q3 业绩超预期 净利润同比 +18% 派息提振股价"
    )
    common_summary = (
        "腾讯控股发布第三季度财报, 营收 +12% / 净利润 +18% 双双超出市场预期, "
        "派息提振股价表现, 多家投行上调目标价."
    )

    articles = [
        ArticleRaw(
            title=common_title,
            original_url="https://x.com/p/qa1-bd-x1-original",
            source_name="雪球",
            published_at=base + timedelta(minutes=0),  # X1 = parent
            summary=common_summary,
            market="HK",
            source_credibility=2,
            is_full_text_available=True,
            hot_score=Decimal(100),
        ),
        ArticleRaw(
            title=common_title,
            original_url="https://x.com/p/qa1-bd-x2-repost",
            source_name="雪球",
            published_at=base + timedelta(minutes=10),  # X2 = child
            summary=common_summary,
            market="HK",
            source_credibility=2,
            is_full_text_available=True,
            hot_score=Decimal(100),
        ),
        ArticleRaw(
            title="美团点评新一轮港股回购计划落地 公司治理获机构投资者认可",
            original_url="https://x.com/p/qa1-bd-y-unrelated",
            source_name="雪球",
            published_at=base + timedelta(minutes=20),
            summary=(
                "美团点评公布新一轮回购计划, 涉资上限 100 亿港元, 反映管理层"
                "对未来现金流的信心, 多家机构投资者对此表示认可."
            ),
            market="HK",
            source_credibility=2,
            is_full_text_available=True,
            hot_score=Decimal(100),
        ),
    ]

    class _StaticSource:
        name = "雪球"

        async def fetch(
            self, *, since: datetime | None = None
        ) -> list[ArticleRaw]:
            return list(articles)

    _patch_dispatcher_register_sources(monkeypatch, [_StaticSource()])
    _patch_dispatcher_invalidate(monkeypatch)
    monkeypatch.setattr(st, "chat", mock_sentiment_llm)

    stats = await dispatcher.run_ingest_articles_job()

    assert stats["inserted"] == 3
    assert stats["simhash_filled"] == 3
    assert stats["topics_linked"] == 1, (
        f"X1/X2 折叠 + Y 不折叠 → 1 行 article_topics, 实际 {stats['topics_linked']}"
    )

    async with session_factory() as s:
        # 验 X1 = parent, X2 = child
        topics = (
            await s.execute(select(ArticleTopic))
        ).scalars().all()
        assert len(topics) == 1
        topic = topics[0]
        # X1 published 比 X2 早 → X1 应 = parent
        x1_id = (
            await s.execute(
                select(Article.article_id).where(
                    Article.original_url == "https://x.com/p/qa1-bd-x1-original"
                )
            )
        ).scalar_one()
        x2_id = (
            await s.execute(
                select(Article.article_id).where(
                    Article.original_url == "https://x.com/p/qa1-bd-x2-repost"
                )
            )
        ).scalar_one()
        assert topic.parent_article_id == x1_id, (
            f"parent 应为 X1 (最早 published_at), 实际 {topic.parent_article_id}"
        )
        assert topic.child_article_id == x2_id

        # Y 完全独立 — 不应出现在 article_topics 任何一列
        y_id = (
            await s.execute(
                select(Article.article_id).where(
                    Article.original_url == "https://x.com/p/qa1-bd-y-unrelated"
                )
            )
        ).scalar_one()
        assert y_id not in {topic.parent_article_id, topic.child_article_id}

    # GET /articles → 2 行 (X1 + Y; X2 折叠不出现)
    resp = await client.get("/api/v1/articles", params={"size": 50})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 2, (
        f"折叠后应剩 X1 + Y 共 2 行, 实际 total={data['total']}"
    )
    urls = {it["original_url"] for it in data["items"]}
    assert urls == {
        "https://x.com/p/qa1-bd-x1-original",
        "https://x.com/p/qa1-bd-y-unrelated",
    }, f"列表 URLs 应仅含 X1 + Y, 实际 {urls}"


# ─── 兜底 sanity: fixture 自身不留脏状态 ─────────────────────────────


async def test_pipeline_fixture_isolation(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001 — 强制走 truncate
) -> None:
    """``truncate_all`` fixture 已清表, 但额外验证 ``articles`` /
    ``article_topics`` 在每个 case 入口都是空表.

    防止上面 5 条 case 偶发依赖, 让 case 之间的隔离假设是显式的而非靠运气.
    """
    async with session_factory() as s:
        a_count = (await s.execute(select(func.count()).select_from(Article))).scalar_one()
        t_count = (
            await s.execute(select(func.count()).select_from(ArticleTopic))
        ).scalar_one()
        assert a_count == 0, f"articles 不为空, len={a_count}"
        assert t_count == 0, f"article_topics 不为空, len={t_count}"


# 防止 flake8 报 "unused import" — uuid 仅被部分用例间接使用
_ = uuid
