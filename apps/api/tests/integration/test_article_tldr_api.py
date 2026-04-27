"""BE-S3-005 文章 TL;DR 端到端集成测.

覆盖 (≥ 4 条; spec/10 §AC 要求 ≥ 4 条):

1. test_post_tldr_scope_ipo_happy_path
   插 5 篇 related_ipos=00700.HK 的文章 (mixed sentiment) → POST /tldr scope=ipo →
   200 + status=ok + bullish/bearish_points 非空 + ratio 和 ≈ 1
2. test_post_tldr_caches_response_redis_hit
   连发两次 POST /tldr → mock LLM 只被调一次 (二次走 Redis 缓存)
3. test_post_tldr_force_refresh_bypasses_cache
   先一次正常调用入缓存, 再用 force_refresh=true → mock LLM 被调第二次
4. test_post_tldr_insufficient_data_when_pool_too_small
   只插 1 篇 → POST → 200 + status=insufficient_data + LLM 不被调用
5. test_post_tldr_scope_market_filters_by_market
   插 HK 3 篇 + A 3 篇 → POST scope=market scope_value=HK → 仅 HK 文章进池
6. test_post_tldr_excludes_child_articles_from_topic
   插 1 篇 parent + 4 篇 child (article_topics 链表) → POST → 实际池 = 1 (parent only),
   走 insufficient_data 兜底 (验证 child 被剔除)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.llm_client import ChatResult, TokenUsage
from app.db.models import Article, ArticleTopic
from app.services import article_tldr_service as tldr

pytestmark = pytest.mark.db


# ─── helpers ──────────────────────────────────────────────────────────────


def _make_chat_result(content: str) -> ChatResult:
    return ChatResult(
        content=content,
        finish_reason="stop",
        usage=TokenUsage.empty(),
        model="zhipu/glm-4-flash",
        provider="zhipu",
        tool_calls=None,
    )


def _build_fake_chat() -> Any:
    """Mock chat: 返回固定 happy path JSON, 把传进来的前 2 个 id 作为 source_ids 回填."""
    call_log: dict[str, int] = {"count": 0}

    async def fake_chat(**kwargs: Any) -> ChatResult:
        call_log["count"] += 1
        messages = kwargs.get("messages", [])
        user_msg = next((m for m in messages if m["role"] == "user"), None)
        body = user_msg["content"].split("\n\n", 1)[1] if user_msg else "[]"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = []
        ids = [item["id"] for item in payload[:2]]

        out = {
            "bullish_ratio": 0.5,
            "neutral_ratio": 0.3,
            "bearish_ratio": 0.2,
            "bullish_points": ["营收同比 +15%", "海外业务扩张"],
            "bearish_points": ["监管处罚风险"],
            "source_article_ids": ids,
        }
        return _make_chat_result(json.dumps(out, ensure_ascii=False))

    fake_chat.call_log = call_log  # type: ignore[attr-defined]
    return fake_chat


async def _insert_article(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    title: str,
    summary: str,
    sentiment: str = "bullish",
    score: float = 0.6,
    market: str = "HK",
    related_ipos: list[dict[str, str]] | None = None,
    hot_score: float = 100.0,
    published_offset_days: int = 0,
    keywords: list[str] | None = None,
) -> uuid.UUID:
    """直接 INSERT 一篇文章; 跳过 dispatcher / dedup / sentiment_tagger 流水线.

    用于 TLDR 测试: 我们只关心候选池 → LLM → 缓存这条路径, 不走 ingest 流程.
    """
    async with session_factory() as s:
        a = Article(
            title=title,
            summary=summary,
            source_name="雪球",
            source_credibility=2,
            original_url=f"https://x.com/p/{uuid.uuid4()}",
            market=market,
            related_ipos=related_ipos or [],
            sentiment=sentiment,
            sentiment_score=Decimal(str(score)),
            keywords=keywords or ["腾讯"],
            hot_score=Decimal(str(hot_score)),
            is_full_text_available=True,
            published_at=datetime.now(UTC) - timedelta(days=published_offset_days),
        )
        s.add(a)
        await s.commit()
        await s.refresh(a)
        return a.article_id


async def _link_child(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
) -> None:
    async with session_factory() as s:
        s.add(
            ArticleTopic(
                parent_article_id=parent_id,
                child_article_id=child_id,
                simhash_distance=1,
            )
        )
        await s.commit()


# ─── 1. happy path scope=ipo ──────────────────────────────────────────────


async def test_post_tldr_scope_ipo_happy_path(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5 篇 related_ipos=00700.HK 的文章 → POST /tldr → 200 + status=ok."""
    fake_chat = _build_fake_chat()
    monkeypatch.setattr(tldr, "chat", fake_chat)

    related = [{"code": "00700.HK", "market": "HK", "name": "腾讯控股"}]
    for i in range(5):
        await _insert_article(
            session_factory,
            title=f"腾讯 Q3 业绩 {i}",
            summary=f"腾讯 Q3 业绩超预期, 派息提振股价表现 — {i}",
            sentiment="bullish" if i < 3 else ("bearish" if i == 3 else "neutral"),
            score=0.7,
            related_ipos=related,
        )

    resp = await client.post(
        "/api/v1/articles/tldr",
        json={"scope": "ipo", "scope_value": "00700.HK"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ok"
    assert data["scope"] == "ipo"
    assert data["scope_value"] == "00700.HK"
    assert data["article_count"] == 5
    total_ratio = (
        data["bullish_ratio"] + data["neutral_ratio"] + data["bearish_ratio"]
    )
    assert abs(total_ratio - 1.0) < 0.05
    assert data["bullish_points"] == ["营收同比 +15%", "海外业务扩张"]
    assert data["bearish_points"] == ["监管处罚风险"]
    assert len(data["source_article_ids"]) == 2  # mock 回填 2 个
    assert "不构成投资建议" in data["message"]
    assert fake_chat.call_log["count"] == 1


# ─── 2. cache hit on 2nd call ─────────────────────────────────────────────


async def test_post_tldr_caches_response_redis_hit(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_chat = _build_fake_chat()
    monkeypatch.setattr(tldr, "chat", fake_chat)

    related = [{"code": "00388.HK", "market": "HK", "name": "港交所"}]
    for i in range(4):
        await _insert_article(
            session_factory,
            title=f"港交所新闻 {i}",
            summary=f"港交所 IPO 业务火热, 新股融资额创新高 {i}",
            related_ipos=related,
        )

    r1 = await client.post(
        "/api/v1/articles/tldr",
        json={"scope": "ipo", "scope_value": "00388.HK"},
    )
    assert r1.status_code == 200
    assert fake_chat.call_log["count"] == 1

    # 二次调用应命中缓存, LLM 不再被调
    r2 = await client.post(
        "/api/v1/articles/tldr",
        json={"scope": "ipo", "scope_value": "00388.HK"},
    )
    assert r2.status_code == 200
    assert fake_chat.call_log["count"] == 1, "second call should hit cache"
    # 内容一致 (注意 generated_at 也是缓存的, 不该变)
    assert r1.json()["generated_at"] == r2.json()["generated_at"]


# ─── 3. force_refresh 旁路缓存 ────────────────────────────────────────────


async def test_post_tldr_force_refresh_bypasses_cache(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_chat = _build_fake_chat()
    monkeypatch.setattr(tldr, "chat", fake_chat)

    for i in range(4):
        await _insert_article(
            session_factory,
            title=f"HK 新股 {i}",
            summary=f"港股新股市场 {i} ",
            market="HK",
        )

    await client.post(
        "/api/v1/articles/tldr", json={"scope": "market", "scope_value": "HK"}
    )
    assert fake_chat.call_log["count"] == 1

    # force_refresh=True → 跳过缓存, LLM 被再调一次
    r2 = await client.post(
        "/api/v1/articles/tldr",
        json={"scope": "market", "scope_value": "HK", "force_refresh": True},
    )
    assert r2.status_code == 200
    assert fake_chat.call_log["count"] == 2


# ─── 4. insufficient_data 兜底 ────────────────────────────────────────────


async def test_post_tldr_insufficient_data_when_pool_too_small(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """池 < 3 篇 → status=insufficient_data + LLM 不被调用."""
    fake_chat = _build_fake_chat()
    monkeypatch.setattr(tldr, "chat", fake_chat)

    related = [{"code": "09660.HK", "market": "HK", "name": "地平线"}]
    await _insert_article(
        session_factory,
        title="地平线递交招股书",
        summary="地平线 (Horizon Robotics) 正式向港交所递交招股书",
        related_ipos=related,
    )

    resp = await client.post(
        "/api/v1/articles/tldr",
        json={"scope": "ipo", "scope_value": "09660.HK"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "insufficient_data"
    assert data["article_count"] == 1
    assert data["bullish_points"] == []
    assert data["bearish_points"] == []
    assert "AI 已为您启动深度分析" in data["message"]
    assert "不构成投资建议" in data["message"]
    assert fake_chat.call_log["count"] == 0  # 池太小不调 LLM


# ─── 5. scope=market 按 market 过滤 ────────────────────────────────────────


async def test_post_tldr_scope_market_filters_by_market(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_chat = _build_fake_chat()
    monkeypatch.setattr(tldr, "chat", fake_chat)

    for i in range(3):
        await _insert_article(
            session_factory,
            title=f"HK 港股新闻 {i}",
            summary=f"港股市场动态 {i}",
            market="HK",
        )
    for i in range(3):
        await _insert_article(
            session_factory,
            title=f"A 股新闻 {i}",
            summary=f"A 股市场动态 {i}",
            market="A",
        )

    resp = await client.post(
        "/api/v1/articles/tldr",
        json={"scope": "market", "scope_value": "HK"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["article_count"] == 3  # 仅 HK 那 3 篇


# ─── 6. child article 被排除 ──────────────────────────────────────────────


async def test_post_tldr_excludes_child_articles_from_topic(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4 篇 child + 1 篇 parent → 候选池实际 = 1, 走 insufficient_data."""
    fake_chat = _build_fake_chat()
    monkeypatch.setattr(tldr, "chat", fake_chat)

    related = [{"code": "01024.HK", "market": "HK", "name": "快手"}]
    parent_id = await _insert_article(
        session_factory,
        title="快手 Q4 财报亮眼",
        summary="快手 Q4 收入超预期, DAU 创新高",
        related_ipos=related,
    )
    for i in range(4):
        cid = await _insert_article(
            session_factory,
            title=f"快手 Q4 财报亮眼 (转发 {i})",
            summary=f"快手 Q4 收入超预期 (转发 {i})",
            related_ipos=related,
        )
        await _link_child(session_factory, parent_id=parent_id, child_id=cid)

    resp = await client.post(
        "/api/v1/articles/tldr",
        json={"scope": "ipo", "scope_value": "01024.HK"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "insufficient_data"
    assert data["article_count"] == 1  # 只有 parent
    assert fake_chat.call_log["count"] == 0
