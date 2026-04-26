"""BE-S2-002 单元测试: LLM facade (chat / embed / rerank + 路由 + 成本估算).

不打真 LLM:
- chat / embed: monkeypatch 到 ``app.adapters.llm_client.acompletion / aembedding``,
  返回 ``SimpleNamespace`` mock (LiteLLM ModelResponse duck-typing 对齐)
- rerank: 用 ``respx`` mock httpx 走的 ``/v1/rerank`` cohere 兼容协议

不验:
- 真模型质量 (留给 BE-S2-009 评测集)
- LangGraph 集成 (留给 BE-S2-007 + QA-S2-001)
- 流式末尾 disclaimer 已在 test_compliance + integration/test_e2e_ipo_diagnose 验过
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from app.adapters import llm_client
from app.adapters.llm_client import (
    ChatResult,
    EmbeddingResult,
    LLMConfigError,
    LLMProviderError,
    RerankResult,
    TokenUsage,
    _credentials_for_provider,
    _estimate_cost_cny,
    _resolve_provider,
    astream_chat_with_meta,
    chat,
    embed,
    rerank,
    stream_chat,
)
from app.core.config import Settings, get_settings

# ─── 共享 fixture: 让 settings 拥有所有 provider 的 key ────────────────────


@pytest.fixture
def llm_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """注入一个三 provider key 都齐全的 Settings, 让路由测试可以走通."""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-siliconflow-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")
    monkeypatch.setenv("ZHIPU_API_KEY", "sk-zhipu-test")
    monkeypatch.setenv(
        "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
    )
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


# ─── 1. provider 路由 / 凭据 ─────────────────────────────────────────────


def test_resolve_provider_three_prefixes() -> None:
    assert _resolve_provider("openai/deepseek-ai/DeepSeek-V3") == "siliconflow"
    assert _resolve_provider("deepseek/deepseek-chat") == "deepseek"
    assert _resolve_provider("zhipu/glm-4-flash") == "zhipu"


def test_resolve_provider_unknown_prefix_raises() -> None:
    with pytest.raises(LLMConfigError) as exc:
        _resolve_provider("anthropic/claude-3")
    assert "无法路由" in str(exc.value)
    assert exc.value.model == "anthropic/claude-3"


def test_credentials_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """三家 provider 任一 key 缺失就抛 LLMConfigError, 不静默回退."""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    get_settings.cache_clear()
    s = get_settings()
    try:
        for prov in ("siliconflow", "deepseek", "zhipu"):
            with pytest.raises(LLMConfigError):
                _credentials_for_provider(prov, s)  # type: ignore[arg-type]
    finally:
        get_settings.cache_clear()


# ─── 2. 成本估算 ─────────────────────────────────────────────────────────


def test_estimate_cost_cny_deepseek_v3() -> None:
    """DeepSeek-V3: input 1.0 / M + output 4.0 / M; 1k+1k = 0.001+0.004 = 0.005."""
    cost = _estimate_cost_cny(
        "openai/deepseek-ai/DeepSeek-V3", prompt_tokens=1000, completion_tokens=1000
    )
    assert cost == Decimal("0.005000")


def test_estimate_cost_cny_bge_m3_pure_input() -> None:
    """bge-m3: 0.5 / M; 10k tokens prompt → 10000/1M*0.5 = 0.005."""
    cost = _estimate_cost_cny(
        "openai/BAAI/bge-m3", prompt_tokens=10000, completion_tokens=0
    )
    assert cost == Decimal("0.005000")


def test_estimate_cost_cny_unknown_model_returns_zero() -> None:
    """未匹配价格表 → 返回 Decimal('0') 不抛, 业务侧不会炸 NOT NULL."""
    cost = _estimate_cost_cny("anthropic/claude-3-opus", 1000, 1000)
    assert cost == Decimal("0.000000")


# ─── 3. chat (非流) ─────────────────────────────────────────────────────


def _make_mock_chat_response(
    *,
    content: str = "hello",
    finish_reason: str = "stop",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    tool_calls: list[Any] | None = None,
) -> SimpleNamespace:
    """构造与 LiteLLM ModelResponse 同形状的 SimpleNamespace."""
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


async def test_chat_happy_returns_structured_result(
    llm_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _make_mock_chat_response(
            content="**摘要**\n本股估值合理。",
            prompt_tokens=820,
            completion_tokens=156,
        )

    monkeypatch.setattr(llm_client, "acompletion", fake_acompletion)

    result = await chat(
        [{"role": "user", "content": "0700 怎么样?"}],
        model="openai/deepseek-ai/DeepSeek-V3",
        max_tokens=1500,
    )

    assert isinstance(result, ChatResult)
    assert result.content == "**摘要**\n本股估值合理。"
    assert result.finish_reason == "stop"
    assert result.provider == "siliconflow"
    assert result.tool_calls is None
    assert result.usage.prompt_tokens == 820
    assert result.usage.completion_tokens == 156
    # cost: 820/M*1 + 156/M*4 = 0.00082 + 0.000624 = 0.001444
    assert result.usage.cost_cny == Decimal("0.001444")
    # base_url + api_key 已注入到 call kwargs
    assert captured["api_key"] == "sk-siliconflow-test"
    assert captured["api_base"] == "https://api.siliconflow.cn/v1"
    assert captured["stream"] is False


async def test_chat_parses_tool_calls(
    llm_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tool_calls 把 SimpleNamespace 与 dict 都规整成统一 dict 形式."""
    fake_tc = SimpleNamespace(
        id="call_abc123",
        type="function",
        function=SimpleNamespace(
            name="get_basic_info", arguments='{"code":"0700.HK"}'
        ),
    )

    async def fake_acompletion(**kwargs: Any) -> SimpleNamespace:
        return _make_mock_chat_response(
            content="",
            finish_reason="tool_calls",
            tool_calls=[fake_tc],
        )

    monkeypatch.setattr(llm_client, "acompletion", fake_acompletion)

    result = await chat(
        [{"role": "user", "content": "查 0700"}],
        tools=[{"type": "function", "function": {"name": "get_basic_info"}}],
    )
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls is not None
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc["id"] == "call_abc123"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_basic_info"
    assert tc["function"]["arguments"] == '{"code":"0700.HK"}'


async def test_chat_provider_error_wraps_upstream(
    llm_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_acompletion(**kwargs: Any) -> SimpleNamespace:
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(llm_client, "acompletion", fake_acompletion)

    with pytest.raises(LLMProviderError) as exc:
        await chat([{"role": "user", "content": "x"}])
    assert "connection reset" in str(exc.value)
    assert exc.value.provider == "siliconflow"
    assert isinstance(exc.value.cause, RuntimeError)


async def test_chat_unknown_model_prefix_raises_config(
    llm_settings: Settings,
) -> None:
    """未知前缀 model → LLMConfigError (路由失败), 不打 LLM."""
    with pytest.raises(LLMConfigError):
        await chat([{"role": "user", "content": "x"}], model="claude/opus-4")


async def test_chat_no_siliconflow_key_raises_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """openai/ 前缀但没配 key → LLMConfigError, 不悄悄落空."""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(LLMConfigError):
            await chat(
                [{"role": "user", "content": "x"}],
                model="openai/deepseek-ai/DeepSeek-V3",
            )
    finally:
        get_settings.cache_clear()


# ─── 4. stream_chat (Sprint 1 兼容契约) ───────────────────────────────────


async def test_stream_chat_yields_friendly_when_no_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没 key 时不抛, yield 引导文 — 防 SSE 路由 break (Sprint 1 契约)."""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    get_settings.cache_clear()
    try:
        out: list[str] = []
        async for tok in stream_chat([{"role": "user", "content": "x"}]):
            out.append(tok)
        full = "".join(out)
        assert "尚未配置" in full
        assert "SILICONFLOW_API_KEY" in full
    finally:
        get_settings.cache_clear()


async def test_stream_chat_appends_disclaimer_at_end(
    llm_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """流末尾追加 disclaimer (Sprint 1 契约 + e2e 用例依赖)."""

    async def fake_stream(**kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        for tok in ["**摘要**\n", "本股估值合理。"]:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=tok))]
            )

    async def fake_acompletion(**kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        return fake_stream(**kwargs)

    monkeypatch.setattr(llm_client, "acompletion", fake_acompletion)

    out: list[str] = []
    async for tok in stream_chat([{"role": "user", "content": "x"}]):
        out.append(tok)
    full = "".join(out)
    assert "不构成投资建议" in full


# ─── 5. astream_chat_with_meta (BE-S2-007 用) ────────────────────────────


async def test_astream_chat_with_meta_yields_deltas_and_final_usage(
    llm_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """流末尾要拿到 usage + finish_reason, 才能写 chat_token_usage."""

    async def fake_stream(**kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        # 3 个 token, 第 3 个带 finish_reason; 第 4 个空 delta + usage (OpenAI v3 格式)
        for tok in ["A", "B", "C"]:
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=tok, tool_calls=None),
                        finish_reason=None,
                    )
                ],
                usage=None,
            )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None, tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=3, total_tokens=13
            ),
        )

    async def fake_acompletion(**kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        assert kwargs.get("stream_options") == {"include_usage": True}
        return fake_stream(**kwargs)

    monkeypatch.setattr(llm_client, "acompletion", fake_acompletion)

    deltas: list[str] = []
    final_usage: TokenUsage | None = None
    final_finish: str | None = None
    async for chunk in astream_chat_with_meta(
        [{"role": "user", "content": "x"}]
    ):
        if chunk.delta:
            deltas.append(chunk.delta)
        if chunk.usage is not None:
            final_usage = chunk.usage
            final_finish = chunk.finish_reason

    assert deltas == ["A", "B", "C"]
    assert final_finish == "stop"
    assert final_usage is not None
    assert final_usage.prompt_tokens == 10
    assert final_usage.completion_tokens == 3


async def test_astream_chat_with_meta_aggregates_tool_calls_across_chunks(
    llm_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenAI 流式 tool_calls 是分片的 (id 第 1 帧, name 第 2 帧, args 多帧拼).

    facade 必须把它按 index 累积成完整一份, 才能给主循环 dispatch.
    """

    async def fake_stream(**kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        # 帧 1: tool_call id + name
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_x",
                                type="function",
                                function=SimpleNamespace(
                                    name="get_basic_info", arguments=""
                                ),
                            )
                        ],
                    ),
                    finish_reason=None,
                )
            ],
            usage=None,
        )
        # 帧 2: arguments 第一段
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(
                                    name=None, arguments='{"code":'
                                ),
                            )
                        ],
                    ),
                    finish_reason=None,
                )
            ],
            usage=None,
        )
        # 帧 3: arguments 第二段
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(
                                    name=None, arguments='"0700.HK"}'
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=80, completion_tokens=12, total_tokens=92
            ),
        )

    async def fake_acompletion(**kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        return fake_stream(**kwargs)

    monkeypatch.setattr(llm_client, "acompletion", fake_acompletion)

    final_chunk = None
    async for chunk in astream_chat_with_meta(
        [{"role": "user", "content": "查 0700"}]
    ):
        if chunk.usage is not None or chunk.tool_calls is not None:
            final_chunk = chunk

    assert final_chunk is not None
    assert final_chunk.tool_calls is not None
    assert len(final_chunk.tool_calls) == 1
    tc = final_chunk.tool_calls[0]
    assert tc["id"] == "call_x"
    assert tc["function"]["name"] == "get_basic_info"
    assert tc["function"]["arguments"] == '{"code":"0700.HK"}'
    assert final_chunk.finish_reason == "tool_calls"


# ─── 6. embed (批量 + 自动分批) ──────────────────────────────────────────


def _make_mock_embed_response(
    n: int, dim: int = 1024, prompt_tokens: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(
        data=[
            {"index": i, "embedding": [0.1 * (i + 1)] * dim, "object": "embedding"}
            for i in range(n)
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, total_tokens=prompt_tokens
        ),
    )


async def test_embed_single_batch_returns_aligned_vectors(
    llm_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, Any]] = []

    async def fake_aembedding(**kwargs: Any) -> SimpleNamespace:
        captured.append(kwargs)
        n = len(kwargs["input"])
        return _make_mock_embed_response(n=n, dim=1024, prompt_tokens=20)

    monkeypatch.setattr(llm_client, "aembedding", fake_aembedding)

    texts = [f"招股书段落 {i}" for i in range(5)]
    result = await embed(texts)

    assert isinstance(result, EmbeddingResult)
    assert len(result.embeddings) == 5
    assert result.dim == 1024
    assert all(len(v) == 1024 for v in result.embeddings)
    assert result.provider == "siliconflow"
    assert result.usage.prompt_tokens == 20
    assert len(captured) == 1
    assert captured[0]["input"] == texts


async def test_embed_auto_batches_when_over_size(
    llm_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """70 条 + batch_size=32 → 32 + 32 + 6 = 3 次 aembedding."""
    call_sizes: list[int] = []

    async def fake_aembedding(**kwargs: Any) -> SimpleNamespace:
        n = len(kwargs["input"])
        call_sizes.append(n)
        return _make_mock_embed_response(n=n, dim=1024, prompt_tokens=10 * n)

    monkeypatch.setattr(llm_client, "aembedding", fake_aembedding)

    texts = [f"chunk-{i}" for i in range(70)]
    result = await embed(texts, batch_size=32)

    assert call_sizes == [32, 32, 6]
    assert len(result.embeddings) == 70
    assert result.usage.prompt_tokens == 700  # 累加


async def test_embed_empty_input_raises_value_error(
    llm_settings: Settings,
) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        await embed([])


async def test_embed_response_count_mismatch_raises_provider_error(
    llm_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """provider 漏返回向量 → 抛 LLMProviderError 而不是悄悄继续 (Sprint 2 防 RAG 入库错位)."""

    async def fake_aembedding(**kwargs: Any) -> SimpleNamespace:
        return _make_mock_embed_response(n=2, dim=1024)  # 输入 3 条但只回 2 条

    monkeypatch.setattr(llm_client, "aembedding", fake_aembedding)

    with pytest.raises(LLMProviderError, match="数量不匹配"):
        await embed(["a", "b", "c"])


# ─── 7. rerank (httpx + respx) ───────────────────────────────────────────


@respx.mock
async def test_rerank_happy_orders_by_score(llm_settings: Settings) -> None:
    """硅基流动 cohere 兼容 rerank: 给 5 个 doc 打分, 返回按分排序的 (orig_idx, score)."""
    respx.post("https://api.siliconflow.cn/v1/rerank").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"index": 2, "relevance_score": 0.95},
                    {"index": 0, "relevance_score": 0.81},
                    {"index": 4, "relevance_score": 0.32},
                ],
                "meta": {"billed_units": {"input_tokens": 120}},
            },
        )
    )

    result = await rerank(
        "腾讯估值",
        ["A doc", "B doc", "C doc", "D doc", "E doc"],
        top_n=3,
    )

    assert isinstance(result, RerankResult)
    assert result.results == [(2, 0.95), (0, 0.81), (4, 0.32)]
    assert result.provider == "siliconflow"
    assert result.usage.prompt_tokens == 120
    # bge-reranker: 1.0/M * 120 / 1M = 0.00012 / 1k = 0.00000012 → quantize 至 0.000000
    # 但实际算: 120 * 1 / 1_000_000 = 0.00012
    assert result.usage.cost_cny == Decimal("0.000120")


@respx.mock
async def test_rerank_http_5xx_wraps_provider_error(
    llm_settings: Settings,
) -> None:
    respx.post("https://api.siliconflow.cn/v1/rerank").mock(
        return_value=httpx.Response(503, text="upstream busy")
    )
    with pytest.raises(LLMProviderError) as exc:
        await rerank("q", ["a", "b"])
    assert "503" in str(exc.value)
    assert exc.value.provider == "siliconflow"


async def test_rerank_no_siliconflow_key_raises_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(LLMConfigError, match="SILICONFLOW_API_KEY"):
            await rerank("q", ["a"])
    finally:
        get_settings.cache_clear()


async def test_rerank_empty_documents_raises(llm_settings: Settings) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        await rerank("q", [])


@respx.mock
async def test_rerank_malformed_response_wraps_provider_error(
    llm_settings: Settings,
) -> None:
    """provider 返回意外结构 → LLMProviderError, 不让上层 KeyError 蒙混."""
    respx.post("https://api.siliconflow.cn/v1/rerank").mock(
        return_value=httpx.Response(
            200, json={"results": [{"score": 0.9}]}  # 缺 index / relevance_score
        )
    )
    with pytest.raises(LLMProviderError, match="malformed"):
        await rerank("q", ["a"])
