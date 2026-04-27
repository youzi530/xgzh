"""BE-S3-004 文章情感打标 — 单元测试 (全 mock LLM, 不打 DB).

覆盖:
- prompt 构造 + 截断
- LLM JSON 输出解析: 正常 / fence 围栏 / 非 JSON / 缺 articles 字段 / 缺 id
- 字段容错: sentiment 别名 / score 反向 / score 越界 / keywords 去重 / keywords 截断
- batch / 单条降级 / fallback neutral 三段式

写库逻辑由 ``tests/integration/test_article_sentiment_e2e.py`` 接真 PG 验证.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.adapters.llm_client import (
    ChatResult,
    LLMProviderError,
    TokenUsage,
)
from app.services.article_ingest import sentiment_tagger as st


def _make_chat_result(content: str) -> ChatResult:
    """造一个最小可用的 ``ChatResult`` (mock chat 函数返回值)."""
    return ChatResult(
        content=content,
        finish_reason="stop",
        usage=TokenUsage.empty(),
        model="zhipu/glm-4-flash",
        provider="zhipu",
        tool_calls=None,
    )


# ─── 1. prompt 构造 ───────────────────────────────────────────────────────


def test_build_user_prompt_carries_id_title_summary() -> None:
    items = [
        st._ArticleInput(id="aaa", title="腾讯控股大涨", summary="财报超预期"),
        st._ArticleInput(id="bbb", title="阿里巴巴回港", summary=""),
    ]
    prompt = st._build_user_prompt(items)
    assert "aaa" in prompt
    assert "bbb" in prompt
    assert "腾讯控股大涨" in prompt
    assert "财报超预期" in prompt
    # JSON 格式必须可二次解析
    body = prompt.split("\n\n", 1)[1]
    decoded = json.loads(body)
    assert isinstance(decoded, list) and len(decoded) == 2
    assert decoded[0]["id"] == "aaa"


def test_build_user_prompt_truncates_oversized_summary() -> None:
    long_summary = "极长正文" * 200  # 800 字, 远超 600
    items = [st._ArticleInput(id="x", title="标题", summary=long_summary)]
    prompt = st._build_user_prompt(items)
    body = prompt.split("\n\n", 1)[1]
    decoded = json.loads(body)
    assert len(decoded[0]["summary"]) <= 600  # _INPUT_TEXT_MAX_LEN
    assert decoded[0]["summary"].endswith("…")


# ─── 2. JSON 解析 ─────────────────────────────────────────────────────────


def test_strip_json_fence_handles_markdown_wrap() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert st._strip_json_fence(raw) == '{"a": 1}'

    raw_plain = '{"a": 1}'
    assert st._strip_json_fence(raw_plain) == '{"a": 1}'


def test_parse_llm_response_happy() -> None:
    content = json.dumps(
        {
            "articles": [
                {
                    "id": "a1",
                    "sentiment": "bullish",
                    "score": 0.8,
                    "keywords": ["利好", "财报", "腾讯"],
                },
                {
                    "id": "a2",
                    "sentiment": "bearish",
                    "score": -0.6,
                    "keywords": ["监管"],
                },
            ]
        },
        ensure_ascii=False,
    )
    parsed = st._parse_llm_response(content, expected_ids={"a1", "a2"})
    assert set(parsed.keys()) == {"a1", "a2"}
    assert parsed["a1"]["sentiment"] == "bullish"
    assert parsed["a1"]["score"] == Decimal("0.800")
    assert parsed["a1"]["keywords"] == ["利好", "财报", "腾讯"]
    assert parsed["a2"]["sentiment"] == "bearish"
    assert parsed["a2"]["score"] == Decimal("-0.600")


def test_parse_llm_response_invalid_json_raises() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        st._parse_llm_response("not a json {{", expected_ids={"x"})


def test_parse_llm_response_missing_articles_field_raises() -> None:
    with pytest.raises(ValueError, match="articles"):
        st._parse_llm_response('{"foo": []}', expected_ids={"x"})


def test_parse_llm_response_drops_unknown_ids() -> None:
    """LLM 偶尔幻觉返回 expected_ids 之外的 id, 必须丢弃 (防注入)."""
    content = json.dumps(
        {
            "articles": [
                {"id": "a1", "sentiment": "bullish", "score": 0.5, "keywords": []},
                {
                    "id": "evil",
                    "sentiment": "bearish",
                    "score": -0.9,
                    "keywords": [],
                },
            ]
        }
    )
    parsed = st._parse_llm_response(content, expected_ids={"a1"})
    assert set(parsed.keys()) == {"a1"}
    assert "evil" not in parsed


# ─── 3. 字段容错 ──────────────────────────────────────────────────────────


def test_coerce_sentiment_aliases() -> None:
    assert st._coerce_sentiment("BULLISH") == "bullish"
    assert st._coerce_sentiment(" Bearish ") == "bearish"
    assert st._coerce_sentiment("positive") == "bullish"
    assert st._coerce_sentiment("看空") == "bearish"
    assert st._coerce_sentiment("unknown") == "neutral"
    assert st._coerce_sentiment(None) == "neutral"
    assert st._coerce_sentiment(123) == "neutral"


def test_coerce_score_clamps_and_aligns_with_sentiment() -> None:
    # 越界 clamp
    assert st._coerce_score(2.5, "bullish") == Decimal("1.000")
    assert st._coerce_score(-3.0, "bearish") == Decimal("-1.000")
    # 反向兜底归零 (bullish 但 score < 0)
    assert st._coerce_score(-0.7, "bullish") == Decimal("0.000")
    assert st._coerce_score(0.5, "bearish") == Decimal("0.000")
    # 正常值
    assert st._coerce_score(0.456, "bullish") == Decimal("0.456")
    # 解析失败
    assert st._coerce_score("not_a_num", "neutral") == Decimal("0.000")


def test_coerce_keywords_dedup_and_truncate() -> None:
    raw = [
        "利好",
        "利好",  # 重复
        "腾讯",
        "极长关键词" * 5,  # 超过 _KEYWORD_MAX_LEN (10) 必截
        "",  # 空串忽略
        123,  # 非 str 忽略
        "财报",
        "并购",
        "监管",  # 第 6 个超 _KEYWORD_MAX_COUNT, 不收
    ]
    out = st._coerce_keywords(raw)
    assert len(out) <= 5
    assert "利好" in out
    assert out.count("利好") == 1  # 去重
    # 截断生效
    assert all(len(kw) <= 10 for kw in out)


def test_coerce_keywords_filters_forbidden_pattern() -> None:
    """LLM 偶尔在 keywords 漏放违规词, 端层 forbidden_pattern_filter 兜底."""
    raw = ["强烈推荐买入", "稳赚", "财报"]
    out = st._coerce_keywords(raw)
    # 违规词被替换 (内容里包含 "[已合规过滤]")
    assert "财报" in out
    assert any("已合规过滤" in kw for kw in out if kw != "财报")


# ─── 4. _tag_batch 三段式: 整批 → 单条降级 → fallback ─────────────────────


@pytest.mark.asyncio
async def test_tag_batch_happy_full_response(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        st._ArticleInput(id="aaa", title="腾讯大涨", summary=""),
        st._ArticleInput(id="bbb", title="阿里财报", summary=""),
    ]
    fake_content = json.dumps(
        {
            "articles": [
                {
                    "id": "aaa",
                    "sentiment": "bullish",
                    "score": 0.7,
                    "keywords": ["腾讯"],
                },
                {
                    "id": "bbb",
                    "sentiment": "neutral",
                    "score": 0.0,
                    "keywords": ["阿里"],
                },
            ]
        }
    )

    call_count = {"n": 0}

    async def fake_chat(**_: Any) -> ChatResult:
        call_count["n"] += 1
        return _make_chat_result(fake_content)

    monkeypatch.setattr(st, "chat", fake_chat)

    result = await st._tag_batch(items, model="zhipu/glm-4-flash")
    assert call_count["n"] == 1  # 整批一次成功, 不走单条
    assert set(result.keys()) == {"aaa", "bbb"}
    assert result["aaa"]["sentiment"] == "bullish"
    assert result["bbb"]["sentiment"] == "neutral"


@pytest.mark.asyncio
async def test_tag_batch_full_failure_falls_back_to_singleton_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整批 LLM 抛异常 → 单条降级 (每篇 1 次 LLM 调用), 单条仍失败 → fallback neutral."""
    items = [
        st._ArticleInput(id="aaa", title="腾讯大涨", summary=""),
        st._ArticleInput(id="bbb", title="阿里财报", summary=""),
    ]

    call_count = {"n": 0}

    async def always_fail(**kwargs: Any) -> ChatResult:
        call_count["n"] += 1
        raise LLMProviderError("upstream 5xx", provider="zhipu", model="x")

    monkeypatch.setattr(st, "chat", always_fail)

    result = await st._tag_batch(items, model="zhipu/glm-4-flash")
    # 1 次整批 + 2 次单条 = 3 次
    assert call_count["n"] == 3
    # 全 fallback neutral
    assert set(result.keys()) == {"aaa", "bbb"}
    assert all(r["sentiment"] == "neutral" for r in result.values())
    assert all(r["score"] == Decimal("0.000") for r in result.values())
    assert all(r["keywords"] == [] for r in result.values())


@pytest.mark.asyncio
async def test_tag_batch_partial_response_singleton_fills_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整批 LLM 回了 1/2 篇 → 漏的那篇走单条调用, 单条成功填上."""
    items = [
        st._ArticleInput(id="aaa", title="腾讯大涨", summary=""),
        st._ArticleInput(id="bbb", title="阿里财报", summary=""),
    ]
    call_state = {"n": 0}

    async def fake_chat(**kwargs: Any) -> ChatResult:
        call_state["n"] += 1
        if call_state["n"] == 1:
            # 整批: 只回 aaa
            content = json.dumps(
                {
                    "articles": [
                        {
                            "id": "aaa",
                            "sentiment": "bullish",
                            "score": 0.5,
                            "keywords": [],
                        }
                    ]
                }
            )
        else:
            # 单条 bbb
            content = json.dumps(
                {
                    "articles": [
                        {
                            "id": "bbb",
                            "sentiment": "bearish",
                            "score": -0.4,
                            "keywords": ["监管"],
                        }
                    ]
                }
            )
        return _make_chat_result(content)

    monkeypatch.setattr(st, "chat", fake_chat)

    result = await st._tag_batch(items, model="zhipu/glm-4-flash")
    assert call_state["n"] == 2
    assert result["aaa"]["sentiment"] == "bullish"
    assert result["bbb"]["sentiment"] == "bearish"
    assert result["bbb"]["keywords"] == ["监管"]


@pytest.mark.asyncio
async def test_tag_one_with_fallback_unexpected_exception_returns_neutral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单条调用碰上未知异常 (非 LLMError / ValueError) 也吃下来, 不抛."""

    async def boom(**kwargs: Any) -> ChatResult:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(st, "chat", boom)

    item = st._ArticleInput(id="x", title="", summary="")
    result = await st._tag_one_with_fallback(item, model="zhipu/glm-4-flash")
    assert result == {
        "sentiment": "neutral",
        "score": Decimal("0.000"),
        "keywords": [],
    }


@pytest.mark.asyncio
async def test_tag_batch_empty_input_returns_empty_dict() -> None:
    result = await st._tag_batch([], model="zhipu/glm-4-flash")
    assert result == {}


# ─── 5. tag_articles_by_id 防 dispatcher 误调 (空输入快路径) ──────────────


@pytest.mark.asyncio
async def test_tag_articles_by_id_empty_list_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空 article_ids 直接返 0, 不查 DB / 不调 LLM."""

    # 故意不 mock 任何东西, 保证空输入下 chat / DB 都不会被碰
    async def explode(**kwargs: Any) -> ChatResult:
        raise AssertionError("不应调 LLM")

    monkeypatch.setattr(st, "chat", explode)
    # session 传 None 也 ok, 因为根本不会用到
    stats = await st.tag_articles_by_id(
        session=None,  # type: ignore[arg-type]
        article_ids=[],
    )
    assert stats == {"tagged": 0, "skipped": 0, "errors": 0}


@pytest.mark.asyncio
async def test_tag_one_with_fallback_value_error_returns_neutral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 返非法 JSON (ValueError) → 单条 fallback."""

    async def bad_json(**kwargs: Any) -> ChatResult:
        return _make_chat_result("not a json")

    monkeypatch.setattr(st, "chat", bad_json)

    item = st._ArticleInput(id="x", title="t", summary="s")
    result = await st._tag_one_with_fallback(item, model="zhipu/glm-4-flash")
    assert result["sentiment"] == "neutral"
    assert result["score"] == Decimal("0.000")


# ─── 6. 类型 / 数据契约 sanity ────────────────────────────────────────────


def test_tag_result_dataclass_is_frozen() -> None:
    """TagResult 必须 frozen — 防业务侧不小心改字段."""
    from dataclasses import FrozenInstanceError

    r = st.TagResult(
        article_id=uuid.uuid4(),
        sentiment="bullish",
        score=Decimal("0.5"),
        keywords=["a"],
    )
    with pytest.raises(FrozenInstanceError):
        r.sentiment = "bearish"  # type: ignore[misc]
