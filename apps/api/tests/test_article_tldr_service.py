"""BE-S3-005 文章 TL;DR 生成 — 单元测试 (全 mock LLM + InMemory Redis, 不打 DB).

覆盖:
- prompt 构造 (id / title / summary 透传)
- LLM JSON 解析: 正常 / fence 围栏 / 非 JSON / 比例越界 / 比例和不为 1 / 幻觉 source_id
- 字段容错: bullish_points 截断 / forbidden_pattern 过滤 / 单条 60 字限制
- ratio 归一化 (全 0 → 全 neutral / 三个非零 → 比例之和 = 1)
- _stat_fallback_from_pool 走 sentiment 字段统计兜底
- _cache_key 唯一 + force_refresh 旁路
- generate_tldr 主入口: insufficient_data 兜底 / LLM 异常走统计兜底 / 缓存命中

走真 DB / 真 LLM 的 e2e 在 ``tests/integration/test_article_tldr_api.py``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from decimal import Decimal
from typing import Any

import pytest

from app.adapters.llm_client import ChatResult, LLMProviderError, TokenUsage
from app.cache.redis_client import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.services import article_tldr_service as tldr


def _make_chat_result(content: str) -> ChatResult:
    return ChatResult(
        content=content,
        finish_reason="stop",
        usage=TokenUsage.empty(),
        model="zhipu/glm-4-flash",
        provider="zhipu",
        tool_calls=None,
    )


def _make_candidates(
    n: int, sentiment: str = "bullish", score: float = 0.6
) -> list[tldr._CandidateArticle]:
    return [
        tldr._CandidateArticle(
            article_id=str(uuid.uuid4()),
            title=f"标题 {i}",
            summary=f"摘要 {i}",
            sentiment=sentiment,
            score=Decimal(str(score)),
            keywords=[f"关键词{i}"],
        )
        for i in range(n)
    ]


# ─── 1. prompt 构造 ────────────────────────────────────────────────────────


def test_build_user_prompt_carries_id_title_summary_keywords() -> None:
    items = _make_candidates(2)
    prompt = tldr._build_user_prompt(items)
    body = prompt.split("\n\n", 1)[1]
    decoded = json.loads(body)
    assert isinstance(decoded, list) and len(decoded) == 2
    assert decoded[0]["id"] == items[0].article_id
    assert decoded[0]["title"] == "标题 0"
    assert decoded[0]["summary"] == "摘要 0"
    assert decoded[0]["sentiment"] == "bullish"
    assert decoded[0]["score"] == 0.6
    assert decoded[0]["keywords"] == ["关键词0"]


# ─── 2. LLM JSON 解析 ──────────────────────────────────────────────────────


def test_parse_llm_response_happy_path() -> None:
    items = _make_candidates(3)
    expected_ids = {it.article_id for it in items}
    payload = {
        "bullish_ratio": 0.5,
        "neutral_ratio": 0.3,
        "bearish_ratio": 0.2,
        "bullish_points": ["营收 +20%", "海外扩张顺利"],
        "bearish_points": ["监管风险"],
        "source_article_ids": [items[0].article_id, items[1].article_id],
    }
    out = tldr._parse_llm_response(json.dumps(payload), expected_ids)
    assert out["bullish_ratio"] == pytest.approx(0.5)
    assert out["neutral_ratio"] == pytest.approx(0.3)
    assert out["bearish_ratio"] == pytest.approx(0.2)
    assert out["bullish_points"] == ["营收 +20%", "海外扩张顺利"]
    assert out["bearish_points"] == ["监管风险"]
    assert out["source_article_ids"] == [items[0].article_id, items[1].article_id]


def test_parse_llm_response_strips_markdown_fence() -> None:
    items = _make_candidates(3)
    expected_ids = {it.article_id for it in items}
    raw = (
        "```json\n"
        '{"bullish_ratio":0.6,"neutral_ratio":0.2,"bearish_ratio":0.2,'
        '"bullish_points":["a"],"bearish_points":["b"],"source_article_ids":[]}'
        "\n```"
    )
    out = tldr._parse_llm_response(raw, expected_ids)
    assert out["bullish_ratio"] == pytest.approx(0.6)
    assert out["bullish_points"] == ["a"]


def test_parse_llm_response_invalid_json_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        tldr._parse_llm_response("not a json {{", set())


def test_parse_llm_response_not_dict_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not a dict"):
        tldr._parse_llm_response("[1,2,3]", set())


def test_parse_llm_response_filters_hallucinated_source_ids() -> None:
    items = _make_candidates(2)
    real_ids = {it.article_id for it in items}
    # 给一个不在池里的 fake id, 必须丢掉
    fake_id = str(uuid.uuid4())
    payload = {
        "bullish_ratio": 0.5,
        "neutral_ratio": 0.5,
        "bearish_ratio": 0.0,
        "bullish_points": [],
        "bearish_points": [],
        "source_article_ids": [items[0].article_id, fake_id, items[0].article_id],
    }
    out = tldr._parse_llm_response(json.dumps(payload), real_ids)
    assert out["source_article_ids"] == [items[0].article_id]  # 去重 + 丢幻觉


def test_parse_llm_response_clamps_out_of_range_ratios() -> None:
    items = _make_candidates(3)
    expected_ids = {it.article_id for it in items}
    payload = {
        "bullish_ratio": 1.5,  # > 1
        "neutral_ratio": -0.2,  # < 0
        "bearish_ratio": "abc",  # 非数字
        "bullish_points": [],
        "bearish_points": [],
        "source_article_ids": [],
    }
    out = tldr._parse_llm_response(json.dumps(payload), expected_ids)
    # _coerce_ratio: 1.5→1.0, -0.2→0.0, "abc"→0.0; 然后归一化
    # b=1.0 n=0.0 br=0.0 → b=1.0 n=0.0 br=0.0
    assert out["bullish_ratio"] == pytest.approx(1.0)
    assert out["neutral_ratio"] == pytest.approx(0.0)
    assert out["bearish_ratio"] == pytest.approx(0.0)


def test_parse_llm_response_normalizes_sum() -> None:
    items = _make_candidates(3)
    expected_ids = {it.article_id for it in items}
    # 三个比例和 = 0.6 + 0.2 + 0.6 = 1.4, 必须归一化
    payload = {
        "bullish_ratio": 0.6,
        "neutral_ratio": 0.2,
        "bearish_ratio": 0.6,
        "bullish_points": [],
        "bearish_points": [],
        "source_article_ids": [],
    }
    out = tldr._parse_llm_response(json.dumps(payload), expected_ids)
    total = out["bullish_ratio"] + out["neutral_ratio"] + out["bearish_ratio"]
    assert total == pytest.approx(1.0, abs=0.01)
    assert out["bullish_ratio"] == pytest.approx(0.6 / 1.4, abs=0.01)


# ─── 3. 字段容错 ──────────────────────────────────────────────────────────


def test_coerce_points_truncates_to_60_chars() -> None:
    long = "A" * 100
    out = tldr._coerce_points([long])
    assert len(out) == 1
    assert len(out[0]) == 60


def test_coerce_points_caps_at_three_items() -> None:
    raw = ["a", "b", "c", "d", "e"]
    out = tldr._coerce_points(raw)
    assert out == ["a", "b", "c"]


def test_coerce_points_dedups_and_filters_forbidden() -> None:
    raw = ["看好后市", "看好后市", "强烈推荐买入腾讯"]
    out = tldr._coerce_points(raw)
    assert "看好后市" in out
    # forbidden_pattern_filter 会把违规词替换为 [已合规过滤]
    cleaned_kw_seen = any("已合规过滤" in p for p in out)
    assert cleaned_kw_seen


def test_coerce_points_drops_non_string_and_empty() -> None:
    raw = ["有效", "", None, 123, "  "]  # type: ignore[list-item]
    out = tldr._coerce_points(raw)
    assert out == ["有效"]


def test_coerce_points_non_list_returns_empty() -> None:
    assert tldr._coerce_points("not a list") == []
    assert tldr._coerce_points(None) == []


def test_coerce_ratio_handles_invalid_inputs() -> None:
    assert tldr._coerce_ratio(None) == 0.0
    assert tldr._coerce_ratio("abc") == 0.0
    assert tldr._coerce_ratio(2.5) == 1.0
    assert tldr._coerce_ratio(-1.0) == 0.0
    assert tldr._coerce_ratio(0.42) == 0.42


def test_normalize_ratios_all_zero_falls_back_neutral() -> None:
    b, n, br = tldr._normalize_ratios(0.0, 0.0, 0.0)
    assert (b, n, br) == (0.0, 1.0, 0.0)


def test_normalize_ratios_sums_to_one() -> None:
    b, n, br = tldr._normalize_ratios(0.6, 0.2, 0.6)
    assert pytest.approx(b + n + br, abs=1e-6) == 1.0


def test_strip_json_fence_no_fence_returns_stripped() -> None:
    assert tldr._strip_json_fence("  {\"a\":1}  ") == '{"a":1}'


# ─── 4. 统计兜底 ───────────────────────────────────────────────────────────


def test_stat_fallback_from_pool_normal_ratios() -> None:
    items = (
        _make_candidates(6, sentiment="bullish")
        + _make_candidates(2, sentiment="bearish")
        + _make_candidates(2, sentiment="neutral")
    )
    out = tldr._stat_fallback_from_pool(items)
    assert out["bullish_ratio"] == pytest.approx(0.6)
    assert out["bearish_ratio"] == pytest.approx(0.2)
    assert out["neutral_ratio"] == pytest.approx(0.2)
    assert out["bullish_points"] == []  # 统计兜底不抽论据
    assert out["bearish_points"] == []
    assert len(out["source_article_ids"]) == 10


def test_stat_fallback_from_pool_empty_returns_neutral() -> None:
    out = tldr._stat_fallback_from_pool([])
    assert out["neutral_ratio"] == 1.0
    assert out["bullish_ratio"] == 0.0
    assert out["bearish_ratio"] == 0.0
    assert out["source_article_ids"] == []


# ─── 5. cache key ─────────────────────────────────────────────────────────


def test_cache_key_unique_per_scope_and_value() -> None:
    k1 = tldr._cache_key("ipo", "00700.HK")
    k2 = tldr._cache_key("ipo", "00388.HK")
    k3 = tldr._cache_key("market", "00700.HK")
    assert k1 != k2
    assert k1 != k3
    assert "00700.HK" in k1
    assert "ipo" in k1


# ─── 6. generate_tldr 主入口 (mock LLM + InMemory Redis) ───────────────────


@pytest.fixture
def fake_redis() -> Any:
    """每条用例一份独立 InMemoryRedisClient, 防 cache 串扰."""
    fake = InMemoryRedisClient()
    set_redis_client(fake)
    yield fake
    reset_redis_client()


@pytest.mark.asyncio
async def test_generate_tldr_insufficient_data_does_not_call_llm(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    """池 < 3 篇 → 直接返 insufficient_data, 不调 LLM, 不写缓存."""
    items = _make_candidates(1)

    async def fake_query(session: Any, **kw: Any) -> list[tldr._CandidateArticle]:
        return items

    monkeypatch.setattr(tldr, "_query_candidates", fake_query)

    llm_called = False

    async def fake_chat(*args: Any, **kwargs: Any) -> ChatResult:
        nonlocal llm_called
        llm_called = True
        return _make_chat_result("{}")

    monkeypatch.setattr(tldr, "chat", fake_chat)

    # session factory mock — 我们把 _query_candidates 整个 mock 了, 不会真打 DB,
    # 但 generate_tldr 会进 ``async with factory()`` 块, 所以 factory 必须存在.
    # 用 conftest 里走单测数据库的 session_factory fixture? 这里不接 DB,
    # 直接 mock factory 返回一个 dummy async ctx mgr.
    class _DummySession:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

    def fake_factory() -> Any:
        return _DummySession()

    monkeypatch.setattr(tldr, "get_session_factory", lambda: fake_factory)

    out = await tldr.generate_tldr(scope="ipo", scope_value="00700.HK")
    assert out["status"] == "insufficient_data"
    assert out["article_count"] == 1
    assert llm_called is False  # 池太小不调 LLM
    assert "不构成投资建议" in out["message"]  # ensure_disclaimer 兜底
    # 不写缓存: 二次调用还是 insufficient_data (注意 mock query 仍返同样 1 篇)
    assert await fake_redis.get(tldr._cache_key("ipo", "00700.HK")) is None


@pytest.mark.asyncio
async def test_generate_tldr_happy_path_caches_payload(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    items = _make_candidates(5, sentiment="bullish")

    async def fake_query(session: Any, **kw: Any) -> list[tldr._CandidateArticle]:
        return items

    llm_call_count = 0

    async def fake_chat(*args: Any, **kwargs: Any) -> ChatResult:
        nonlocal llm_call_count
        llm_call_count += 1
        payload = {
            "bullish_ratio": 0.8,
            "neutral_ratio": 0.1,
            "bearish_ratio": 0.1,
            "bullish_points": ["营收增长", "市场扩张"],
            "bearish_points": ["竞争加剧"],
            "source_article_ids": [items[0].article_id, items[1].article_id],
        }
        return _make_chat_result(json.dumps(payload))

    class _DummySession:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr(tldr, "_query_candidates", fake_query)
    monkeypatch.setattr(tldr, "chat", fake_chat)
    monkeypatch.setattr(tldr, "get_session_factory", lambda: lambda: _DummySession())

    out1 = await tldr.generate_tldr(scope="ipo", scope_value="00700.HK")
    assert out1["status"] == "ok"
    assert out1["article_count"] == 5
    assert out1["bullish_ratio"] == pytest.approx(0.8)
    assert "营收增长" in out1["bullish_points"]
    assert llm_call_count == 1

    # 二次调用走缓存, LLM 不再被调
    out2 = await tldr.generate_tldr(scope="ipo", scope_value="00700.HK")
    assert llm_call_count == 1, "缓存命中后 LLM 不应被再次调用"
    assert out2["bullish_ratio"] == out1["bullish_ratio"]
    assert out2["source_article_ids"] == out1["source_article_ids"]


@pytest.mark.asyncio
async def test_generate_tldr_force_refresh_bypasses_cache(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    items = _make_candidates(5)

    async def fake_query(session: Any, **kw: Any) -> list[tldr._CandidateArticle]:
        return items

    llm_call_count = 0

    async def fake_chat(*args: Any, **kwargs: Any) -> ChatResult:
        nonlocal llm_call_count
        llm_call_count += 1
        payload = {
            "bullish_ratio": 0.5,
            "neutral_ratio": 0.5,
            "bearish_ratio": 0.0,
            "bullish_points": [],
            "bearish_points": [],
            "source_article_ids": [],
        }
        return _make_chat_result(json.dumps(payload))

    class _DummySession:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr(tldr, "_query_candidates", fake_query)
    monkeypatch.setattr(tldr, "chat", fake_chat)
    monkeypatch.setattr(tldr, "get_session_factory", lambda: lambda: _DummySession())

    await tldr.generate_tldr(scope="market", scope_value="HK")
    assert llm_call_count == 1
    # force_refresh 旁路缓存
    await tldr.generate_tldr(scope="market", scope_value="HK", force_refresh=True)
    assert llm_call_count == 2


@pytest.mark.asyncio
async def test_generate_tldr_llm_failure_falls_back_to_stat(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    """LLM 抛 ``LLMProviderError`` (LLMError 子类) → 走 _stat_fallback_from_pool 兜底."""
    items = (
        _make_candidates(6, sentiment="bullish")
        + _make_candidates(4, sentiment="bearish")
    )

    async def fake_query(session: Any, **kw: Any) -> list[tldr._CandidateArticle]:
        return items

    async def fake_chat(*args: Any, **kwargs: Any) -> ChatResult:
        raise LLMProviderError("zhipu down", provider="zhipu")

    class _DummySession:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr(tldr, "_query_candidates", fake_query)
    monkeypatch.setattr(tldr, "chat", fake_chat)
    monkeypatch.setattr(tldr, "get_session_factory", lambda: lambda: _DummySession())

    out = await tldr.generate_tldr(scope="custom", scope_value="腾讯回购")
    assert out["status"] == "ok"  # 兜底也算成功状态, 但 points 为空
    assert out["bullish_ratio"] == pytest.approx(0.6)
    assert out["bearish_ratio"] == pytest.approx(0.4)
    assert out["bullish_points"] == []  # 统计兜底无法抽论据
    assert out["bearish_points"] == []
    # 兜底也写缓存 (避免 LLM 持续异常时反复调用)
    cache_key = tldr._cache_key("custom", "腾讯回购")
    assert await fake_redis.get(cache_key) is not None


@pytest.mark.asyncio
async def test_generate_tldr_empty_scope_value_raises() -> None:
    with pytest.raises(ValueError, match="scope_value 不能为空"):
        await tldr.generate_tldr(scope="ipo", scope_value="   ")


# ─── 7. 数据结构 frozen ───────────────────────────────────────────────────


def test_candidate_article_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    c = _make_candidates(1)[0]
    with pytest.raises(FrozenInstanceError):
        c.title = "tampered"  # type: ignore[misc]


def test_candidate_article_asdict_serializable() -> None:
    c = _make_candidates(1)[0]
    d = asdict(c)
    assert "article_id" in d and "title" in d and "summary" in d
