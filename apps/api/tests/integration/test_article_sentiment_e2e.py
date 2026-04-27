"""BE-S3-004 文章情感打标端到端集成测.

覆盖 (≥ 3 条; spec/10 §AC 要求 ≥ 3 条):

1. test_dispatcher_inline_tags_inserted_articles
   全 mock LLM, dispatcher 跑完 → 本批新文 sentiment / score / keywords 三字段全填
2. test_backfill_job_picks_up_unlabeled_articles
   先入库一篇 sentiment=NULL (绕过 dispatcher), 再跑 ``run_sentiment_tag_job``,
   该文章 sentiment 被补上
3. test_dispatcher_llm_failure_falls_back_to_neutral
   mock LLM 整批 + 单条都抛异常 → 本批新文都得到 neutral 兜底, 不阻塞 dispatcher
4. test_dispatcher_skips_already_tagged_articles
   article 已有 sentiment → ``tag_articles_by_id`` 不重复调 LLM (cost 浪费 + 状态错乱)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.llm_client import ChatResult, LLMProviderError, TokenUsage
from app.db.models import IPO, Article
from app.services.article_ingest import dispatcher
from app.services.article_ingest import sentiment_tagger as st
from app.services.article_ingest.sentiment_tagger import (
    run_sentiment_tag_job,
    tag_articles_by_id,
)
from app.services.article_ingest.sources.base import ArticleRaw

pytestmark = pytest.mark.db


# ─── helpers ──────────────────────────────────────────────────────────────


async def _seed_ipos(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """种 1 只活跃 IPO 给关键词反查用."""
    async with session_factory() as s:
        s.add(
            IPO(
                code="00700.HK",
                name="腾讯控股",
                market="HK",
                status="upcoming",
                listing_date=date(2026, 5, 1),
                data_source="seed-sentiment-e2e",
            )
        )
        await s.commit()


def _make_raw(
    title: str,
    url: str,
    *,
    summary: str | None = None,
    market: str = "HK",
) -> ArticleRaw:
    return ArticleRaw(
        title=title,
        original_url=url,
        source_name="雪球",
        published_at=datetime.now(UTC),
        summary=summary,
        market=market,
        source_credibility=2,
        is_full_text_available=True,
        hot_score=Decimal(100),
    )


class _StaticSource:
    """假 source: 构造时给定 articles, ``fetch()`` 直接返."""

    def __init__(self, name: str, articles: list[ArticleRaw]) -> None:
        self.name = name
        self._articles = articles

    async def fetch(self, *, since: datetime | None = None) -> list[ArticleRaw]:
        return list(self._articles)


def _patch_register_sources(
    monkeypatch: pytest.MonkeyPatch, sources: list[_StaticSource]
) -> None:
    monkeypatch.setattr(dispatcher, "register_sources", lambda **kw: sources)


def _patch_invalidate(monkeypatch: pytest.MonkeyPatch) -> None:
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


def _build_fake_chat(
    sentiment_by_url: dict[str, str], score_by_url: dict[str, float] | None = None
):
    """构造一个 mock chat: 输入文章 ID 列表 → LLM 输出对应 sentiment.

    LLM 输入里我们传的 id = str(article_id), 所以测试侧没法预知 article_id;
    采用 "任意 id 都返 ``sentiment_by_url`` 的统一值" 简化法 — 在测试场景里
    每批文章的 sentiment 都一样即可走通流水线.
    """

    if score_by_url is None:
        score_by_url = {}

    async def fake_chat(**kwargs: Any) -> ChatResult:
        messages = kwargs.get("messages", [])
        user_msg = next((m for m in messages if m["role"] == "user"), None)
        body = user_msg["content"].split("\n\n", 1)[1] if user_msg else "[]"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = []

        # 默认值: 没在 dict 配的 → bullish + 0.7
        default_sentiment = next(iter(sentiment_by_url.values()), "bullish")
        default_score = next(iter(score_by_url.values()), 0.7)

        articles_out = []
        for item in payload:
            articles_out.append(
                {
                    "id": item["id"],
                    "sentiment": default_sentiment,
                    "score": default_score,
                    "keywords": ["腾讯", "财报"],
                }
            )
        content = json.dumps({"articles": articles_out}, ensure_ascii=False)
        return _make_chat_result(content)

    return fake_chat


# ─── 1. dispatcher inline 打标 happy path ────────────────────────────────


async def test_dispatcher_inline_tags_inserted_articles(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dispatcher 跑完 → 本批新文 sentiment 三字段全填, stats.sentiment_tagged > 0."""
    await _seed_ipos(session_factory)

    src = _StaticSource(
        "雪球",
        [
            _make_raw(
                "腾讯控股 Q3 业绩超预期 净利润同比 +15% 派息提振股价",
                "https://x.com/p/q3-1",
                summary="腾讯控股 Q3 业绩超预期, 派息提振股价表现, 多家投行上调目标价.",
            ),
            _make_raw(
                "腾讯游戏部门收入环比增长 22% 海外业务持续扩张",
                "https://x.com/p/games-1",
                summary="腾讯游戏部门 Q3 收入环比 +22%, 海外市场快速扩张, 全球化战略见效.",
            ),
        ],
    )
    _patch_register_sources(monkeypatch, [src])
    _patch_invalidate(monkeypatch)
    monkeypatch.setattr(
        st, "chat", _build_fake_chat({"_": "bullish"}, {"_": 0.85})
    )

    stats = await dispatcher.run_ingest_articles_job()

    assert stats["inserted"] == 2
    assert stats["sentiment_tagged"] == 2
    assert stats["errors"] == 0

    async with session_factory() as s:
        rows = (
            await s.execute(
                select(
                    Article.original_url,
                    Article.sentiment,
                    Article.sentiment_score,
                    Article.keywords,
                ).order_by(Article.published_at.desc())
            )
        ).all()

    assert len(rows) == 2
    for url, sentiment, score, keywords in rows:
        assert sentiment == "bullish", f"{url} sentiment={sentiment}"
        assert score == Decimal("0.850"), f"{url} score={score}"
        assert keywords == ["腾讯", "财报"], f"{url} keywords={keywords}"


# ─── 2. backfill job 兜底已入库的 sentiment IS NULL ───────────────────────


async def test_backfill_job_picks_up_unlabeled_articles(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """绕过 dispatcher 直接写 article (sentiment=NULL), 跑 ``run_sentiment_tag_job``
    后该文章 sentiment 被补上."""
    await _seed_ipos(session_factory)

    article_id = uuid.uuid4()
    async with session_factory() as s:
        s.add(
            Article(
                article_id=article_id,
                source_name="雪球",
                source_credibility=2,
                original_url="https://x.com/p/orphan",
                title="腾讯控股回购股份提振股价 投行上调目标价",
                summary="腾讯控股宣布回购股份, 多家投行上调目标价.",
                published_at=datetime.now(UTC),
                market="HK",
                related_ipos=[{"code": "00700.HK", "market": "HK", "name": "腾讯控股"}],
                sentiment=None,  # 关键: NULL = 还没打标
                sentiment_score=None,
                keywords=[],
                simhash=None,
                hot_score=Decimal(100),
                is_full_text_available=True,
            )
        )
        await s.commit()

    # mock LLM 返回 bearish (与默认 bullish 不同, 验证 LLM 真的被调用)
    monkeypatch.setattr(
        st, "chat", _build_fake_chat({"_": "bearish"}, {"_": -0.6})
    )

    stats = await run_sentiment_tag_job()

    assert stats["scanned"] == 1
    assert stats["tagged"] == 1
    assert stats["errors"] == 0

    async with session_factory() as s:
        row = (
            await s.execute(
                select(Article.sentiment, Article.sentiment_score).where(
                    Article.article_id == article_id
                )
            )
        ).one()
    assert row.sentiment == "bearish"
    assert row.sentiment_score == Decimal("-0.600")


# ─── 3. LLM 全炸 → fallback neutral, 不阻塞 dispatcher ───────────────────


async def test_dispatcher_llm_failure_falls_back_to_neutral(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 整批 + 单条都抛 → 文章入库后 sentiment=neutral / score=0.0 / keywords=[];
    dispatcher 主流程 stats.errors=0 (打标失败不应让整个 ingest job 失败)."""
    await _seed_ipos(session_factory)

    src = _StaticSource(
        "雪球",
        [
            _make_raw(
                "腾讯控股发布公告 关于股东大会议程的更新",
                "https://x.com/p/announce",
                summary="腾讯控股股东大会议程更新, 涉及董事会人事变动.",
            ),
        ],
    )
    _patch_register_sources(monkeypatch, [src])
    _patch_invalidate(monkeypatch)

    async def always_fail(**kwargs: Any) -> ChatResult:
        raise LLMProviderError("upstream 5xx", provider="zhipu", model="x")

    monkeypatch.setattr(st, "chat", always_fail)

    stats = await dispatcher.run_ingest_articles_job()

    # ingest 主流程不挂 (LLM 失败被 sentiment_tagger 内部消化)
    assert stats["inserted"] == 1
    assert stats["sentiment_tagged"] == 1  # 仍计入 (即使是 neutral fallback)
    assert stats["errors"] == 0

    async with session_factory() as s:
        row = (
            await s.execute(
                select(
                    Article.sentiment, Article.sentiment_score, Article.keywords
                ).where(Article.original_url == "https://x.com/p/announce")
            )
        ).one()
    assert row.sentiment == "neutral"
    assert row.sentiment_score == Decimal("0.000")
    assert row.keywords == []


# ─── 4. 跳过已打标的 (幂等; 不重复调 LLM 浪费 cost) ───────────────────────


async def test_dispatcher_skips_already_tagged_articles(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已打标的 article 不应再走 LLM (cost 浪费 + 状态错乱)."""
    await _seed_ipos(session_factory)

    article_id = uuid.uuid4()
    async with session_factory() as s:
        s.add(
            Article(
                article_id=article_id,
                source_name="雪球",
                source_credibility=2,
                original_url="https://x.com/p/already-tagged",
                title="腾讯控股 Q3 业绩",
                summary="财报披露",
                published_at=datetime.now(UTC),
                market="HK",
                related_ipos=[{"code": "00700.HK", "market": "HK", "name": "腾讯控股"}],
                sentiment="bullish",  # 关键: 已有 sentiment
                sentiment_score=Decimal("0.500"),
                keywords=["原始关键词"],
                simhash=None,
                hot_score=Decimal(100),
                is_full_text_available=True,
            )
        )
        await s.commit()

    chat_call_count = {"n": 0}

    async def counting_chat(**kwargs: Any) -> ChatResult:
        chat_call_count["n"] += 1
        return _make_chat_result(
            json.dumps(
                {
                    "articles": [
                        {
                            "id": str(article_id),
                            "sentiment": "bearish",
                            "score": -0.9,
                            "keywords": ["新关键词"],
                        }
                    ]
                }
            )
        )

    monkeypatch.setattr(st, "chat", counting_chat)

    async with session_factory() as s:
        stats = await tag_articles_by_id(
            s, article_ids=[article_id]
        )
        await s.commit()

    # 已打标过 → 跳过, 不调 LLM
    assert chat_call_count["n"] == 0
    assert stats["skipped"] == 1
    assert stats["tagged"] == 0

    # 字段没动
    async with session_factory() as s:
        row = (
            await s.execute(
                select(Article.sentiment, Article.keywords).where(
                    Article.article_id == article_id
                )
            )
        ).one()
    assert row.sentiment == "bullish"  # 没被覆盖成 bearish
    assert row.keywords == ["原始关键词"]
