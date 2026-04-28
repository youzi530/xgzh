"""BE-S4-004 集成测试: AI 历史规律分析 SSE.

用例 (≥ 5):
1. test_happy_full_chain
   ≥ 5 候选 + mock LLM → start / delta×N / citations / end 全帧, content 透传 OK
2. test_insufficient_data_error
   候选 < 5 → ``event: error code=insufficient_data`` + 不调 LLM
3. test_cache_hit_no_llm_second_call
   同 params 2 次调用 → LLM 仅调 1 次 (@cached 命中)
4. test_forbidden_pattern_filter_cleans_before_sse
   LLM 输出含违禁词 ("必涨" / "稳赚") → SSE delta 仅含 "[已合规过滤]", 不泄
5. test_unauthenticated_401
   无 Bearer token → 401 token_missing
6. test_rate_limit_429
   单用户 6 次/min 第 6 次 → 429

未验:
- LLM 双 fallback 双失败 → ``event: error code=llm_error`` (mock 太繁琐, 单测覆盖)
- 真 ``deepseek-reasoner`` / ``glm-4-flash`` 上游 (那是 adapter 层职责; 这里 mock)
- ``current_ipo_code`` 在 prompt 中的位置 (端到端不验内容质量, 留单测)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import llm_client
from app.db.models import IPO, User
from app.security.jwt import create_access_token

pytestmark = pytest.mark.db


# ─── helpers: 种用户 + 候选池 ────────────────────────────────────────


async def _seed_user_and_token(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    phone_suffix: str = "8001",
) -> tuple[uuid.UUID, str]:
    async with session_factory() as s:
        u = User(
            phone=f"+8613900{phone_suffix}",
            invite_code=f"HP{phone_suffix}",
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        uid = u.user_id
    token, _ = create_access_token(user_id=uid)
    return uid, token


async def _seed_candidates(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    industry: str = "互联网",
    count: int = 8,
) -> None:
    """种 N 条 listed IPO 凑足候选池 (默认 8 ≥ 5)."""
    rows = []
    for i in range(count):
        rows.append(
            IPO(
                code=f"0{i + 100}.HK",
                name=f"测试IPO-{i}",
                market="HK",
                industry_l1=industry,
                industry_l2="子行业",
                listing_date=date(2023, ((i % 12) + 1), 15),
                pe_ratio=Decimal("25.0") + Decimal(i),
                raised_amount=Decimal("1000000000"),
                first_day_change_pct=Decimal("10.0") + Decimal(i * 5),
                one_lot_winning_rate=Decimal("0.4"),
                oversubscribe_multiple=Decimal("100.0"),
                sponsors=["高盛", "中金公司"],
                status="listed",
                data_source="hp-test",
            )
        )
    async with session_factory() as s:
        s.add_all(rows)
        await s.commit()


# ─── helper: SSE body → frames ────────────────────────────────────


def _parse_sse_frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    """SSE 帧切分 (与 test_e2e_ipo_diagnose 同款逻辑, inline 化)."""
    body_norm = body.replace("\r\n", "\n")
    frames: list[tuple[str, dict[str, Any]]] = []
    for chunk in body_norm.split("\n\n"):
        if not chunk.strip():
            continue
        event_type = ""
        data_str = ""
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                event_type = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_str = line[len("data:"):].strip()
        if not event_type:
            continue
        try:
            data = json.loads(data_str) if data_str else {}
        except json.JSONDecodeError:
            data = {"_raw": data_str}
        frames.append((event_type, data))
    return frames


# ─── helper: programmable LLM mock ──────────────────────────────────


class _LLMCallTracker:
    """记 ``llm_client.chat`` 被调几次 + 返定义内容."""

    def __init__(self, content: str = "**测试报告**\n\n基本面良好.") -> None:
        self.calls: list[dict[str, Any]] = []
        self.content = content

    async def __call__(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 1500,
        **kwargs: Any,
    ) -> llm_client.ChatResult:
        self.calls.append(
            {
                "model": model,
                "msgs_count": len(messages),
                "temp": temperature,
            }
        )
        return llm_client.ChatResult(
            content=self.content,
            finish_reason="stop",
            usage=llm_client.TokenUsage(100, 200, 300, Decimal("0.001")),
            model=model or "deepseek-reasoner",
            provider="deepseek",
        )


@pytest.fixture
def llm_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> _LLMCallTracker:
    """替换 ``llm_client.chat`` 为可观测的 mock; 默认返合规 5 段假报告."""
    tracker = _LLMCallTracker(
        content=(
            "### 📊 行业首日涨幅分布\n"
            "mean=22.5%, median=18.7%; 这个行业的打新热度中等偏上.\n\n"
            "### 📈 估值 vs 涨幅相关性\n"
            "PE 与首日涨幅呈弱负相关; 高 PE 反而首日涨幅较低.\n\n"
            "### 🏆 顶部分位 (前 25% 涨幅) 共性\n"
            "腾讯 [00700.HK] / 美团 [03690.HK] / 快手 [01024.HK] 均为社交电商赛道.\n\n"
            "### ⚠️ 底部分位 (后 25% 涨幅) 共性 + 风险信号\n"
            "高估值 + 募资 > 100 亿组合, 上市当日承压.\n\n"
            "### 💡 Top 3 启示 + 当前 IPO 参考\n"
            "1. 关注估值与赛道的匹配; 2. 警惕高 PE 的大盘股; 3. 中签率与认购倍数共同看.\n\n"
            "以上为客观分析, 最终决策请结合自身情况, 本工具不构成投资建议."
        )
    )
    monkeypatch.setattr(llm_client, "chat", tracker)
    return tracker


@pytest.fixture
async def clear_hp_cache() -> AsyncIterator[None]:
    """清干净 ``agent:hp`` 缓存 + ``agent_hp`` rate-limit 计数 — 让每个 case 起点独立."""
    from app.cache.redis_client import get_redis_client

    client = get_redis_client()
    # InMemoryRedisClient: 清 cache:agent:hp:* + rate:agent_hp:* 前缀
    if hasattr(client, "_store"):
        keys_to_del = [
            k for k in list(client._store.keys())
            if "agent:hp" in k or "agent_hp" in k
        ]
        for k in keys_to_del:
            client._store.pop(k, None)
    yield
    if hasattr(client, "_store"):
        keys_to_del = [
            k for k in list(client._store.keys())
            if "agent:hp" in k or "agent_hp" in k
        ]
        for k in keys_to_del:
            client._store.pop(k, None)


# ─── 1. happy path ──────────────────────────────────────────────────


async def test_happy_full_chain(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    llm_tracker: _LLMCallTracker,
    clear_hp_cache: None,  # noqa: ARG001
) -> None:
    """≥ 5 候选 + mock LLM → start / delta×N / citations / end 全帧."""
    _, token = await _seed_user_and_token(session_factory, phone_suffix="0001")
    await _seed_candidates(session_factory, count=8)

    r = await client.post(
        "/api/v1/agent/historical-pattern",
        json={"industry": "互联网", "year_from": 2022, "year_to": 2024},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    frames = _parse_sse_frames(r.text)
    events = [f[0] for f in frames]

    # 协议契约: start, delta×N, citations, end
    assert events[0] == "start"
    assert events[-1] == "end"
    assert "citations" in events
    delta_count = events.count("delta")
    assert delta_count >= 3, f"应有 ≥ 3 帧 delta, 实际 {delta_count}"

    # start 帧元数据
    start = next(d for e, d in frames if e == "start")
    assert start["industry"] == "互联网"
    assert start["peer_count"] == 8
    assert start["sample_size"] == 50

    # 拼接 delta content 应包含 5 段标题
    full_text = "".join(d.get("content", "") for e, d in frames if e == "delta")
    assert "📊 行业首日涨幅分布" in full_text
    assert "📈 估值 vs 涨幅相关性" in full_text
    assert "🏆 顶部分位" in full_text
    assert "⚠️ 底部分位" in full_text
    assert "💡 Top 3 启示" in full_text
    assert "不构成投资建议" in full_text

    # citations 含至少 1 条 source
    citations = next(d for e, d in frames if e == "citations")
    assert len(citations["sources"]) >= 1
    assert citations["total"] == 8

    # end 帧含 model + warnings
    end = next(d for e, d in frames if e == "end")
    assert end["ok"] is True
    assert "deepseek-reasoner" in end["model"]
    assert end["warnings"] == []  # mock 没违禁词不需 fallback

    # LLM 调用 1 次
    assert len(llm_tracker.calls) == 1
    assert llm_tracker.calls[0]["model"] == "deepseek-reasoner"


# ─── 2. insufficient_data ───────────────────────────────────────────


async def test_insufficient_data_error(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    llm_tracker: _LLMCallTracker,
    clear_hp_cache: None,  # noqa: ARG001
) -> None:
    """候选 < 5 → error event, 不调 LLM."""
    _, token = await _seed_user_and_token(session_factory, phone_suffix="0002")
    await _seed_candidates(session_factory, count=3)  # < 5

    r = await client.post(
        "/api/v1/agent/historical-pattern",
        json={"industry": "互联网"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    frames = _parse_sse_frames(r.text)

    error_events = [d for e, d in frames if e == "error"]
    assert len(error_events) == 1
    assert error_events[0]["code"] == "insufficient_data"
    assert error_events[0]["peer_count"] == 3

    # LLM 一次都没调
    assert len(llm_tracker.calls) == 0


# ─── 3. cache hit ───────────────────────────────────────────────────


async def test_cache_hit_no_llm_second_call(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    llm_tracker: _LLMCallTracker,
    clear_hp_cache: None,  # noqa: ARG001
) -> None:
    """同 params 2 次 → LLM 仅 1 次, 第 2 次走 @cached 命中."""
    _, token = await _seed_user_and_token(session_factory, phone_suffix="0003")
    await _seed_candidates(session_factory, count=8, industry="医药")

    body = {"industry": "医药", "year_from": 2023, "year_to": 2024}
    headers = {"Authorization": f"Bearer {token}"}

    r1 = await client.post(
        "/api/v1/agent/historical-pattern", json=body, headers=headers
    )
    r2 = await client.post(
        "/api/v1/agent/historical-pattern", json=body, headers=headers
    )

    assert r1.status_code == 200
    assert r2.status_code == 200
    # 第 2 次 SSE 仍然 well-formed (start / delta / citations / end)
    frames2 = _parse_sse_frames(r2.text)
    events2 = [f[0] for f in frames2]
    assert events2[0] == "start"
    assert events2[-1] == "end"
    assert "delta" in events2

    # LLM 总计仅调 1 次 (cache hit)
    assert len(llm_tracker.calls) == 1, (
        f"@cached 应命中, LLM 实际调 {len(llm_tracker.calls)} 次"
    )


# ─── 4. forbidden_pattern_filter ────────────────────────────────────


async def test_forbidden_pattern_filter_cleans_before_sse(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    clear_hp_cache: None,  # noqa: ARG001
) -> None:
    """LLM 输出含违禁词 → SSE delta 仅 "[已合规过滤]" 不泄."""
    _, token = await _seed_user_and_token(session_factory, phone_suffix="0004")
    await _seed_candidates(session_factory, count=8, industry="新能源")

    # mock LLM 返带违禁词的 content (按 FORBIDDEN_PATTERNS 真实命中:
    # - "必涨" / "稳赚" / "包赚" 直接命中
    # - "建议满仓" 命中 ``建议(满仓|重仓|全仓|加仓|抄底)``
    # - "all in" 命中 ``all\s*in|梭哈``)
    bad_tracker = _LLMCallTracker(
        content=(
            "### 📊 行业分布\n这个赛道必涨, 建议满仓!\n\n"
            "### 💡 启示\n稳赚不赔的机会, all in 干!"
        )
    )
    monkeypatch.setattr(llm_client, "chat", bad_tracker)

    r = await client.post(
        "/api/v1/agent/historical-pattern",
        json={"industry": "新能源"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    frames = _parse_sse_frames(r.text)
    full_text = "".join(d.get("content", "") for e, d in frames if e == "delta")

    # 违禁词被替换 (这些是真在 FORBIDDEN_PATTERNS 里的)
    assert "必涨" not in full_text
    assert "稳赚" not in full_text
    assert "建议满仓" not in full_text
    assert "all in" not in full_text.lower()
    assert "[已合规过滤]" in full_text

    # end.warnings 含 forbidden_patterns_filtered 标记
    end = next(d for e, d in frames if e == "end")
    assert any(
        "forbidden_patterns_filtered" in w for w in end.get("warnings", [])
    )


# ─── 5. unauthenticated ─────────────────────────────────────────────


async def test_unauthenticated_401(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    llm_tracker: _LLMCallTracker,  # noqa: ARG001
    clear_hp_cache: None,  # noqa: ARG001
) -> None:
    """无 Bearer token → 401 token_missing."""
    await _seed_candidates(session_factory, count=8, industry="科技")

    r = await client.post(
        "/api/v1/agent/historical-pattern",
        json={"industry": "科技"},
    )
    assert r.status_code == 401
    body = r.json()
    assert body["detail"]["code"] == "token_missing"


# ─── 6. rate-limit 429 ──────────────────────────────────────────────


async def test_rate_limit_429(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    llm_tracker: _LLMCallTracker,
    clear_hp_cache: None,  # noqa: ARG001
) -> None:
    """单用户 6 次/min: 第 6 次 → 429 rate_limit_exceeded."""
    _, token = await _seed_user_and_token(session_factory, phone_suffix="0006")
    await _seed_candidates(session_factory, count=8, industry="金融")

    headers = {"Authorization": f"Bearer {token}"}
    body = {"industry": "金融"}

    # 前 5 次成功
    statuses = []
    for _ in range(5):
        r = await client.post(
            "/api/v1/agent/historical-pattern", json=body, headers=headers
        )
        statuses.append(r.status_code)
    assert statuses == [200] * 5

    # 第 6 次 → 429
    r6 = await client.post(
        "/api/v1/agent/historical-pattern", json=body, headers=headers
    )
    assert r6.status_code == 429, f"第 6 次应 429, 实际 {r6.status_code}: {r6.text}"

    # LLM 仅 1 次 (前 5 次后 4 次走 cache; 第 6 次 rate_limit 挡前)
    assert len(llm_tracker.calls) == 1
