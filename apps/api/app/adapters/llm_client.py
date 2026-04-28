"""LLM 适配器 facade (BE-S2-002): chat / embedding / rerank 三入口.

设计目标
========
1. 三入口统一对接 *硅基流动 / DeepSeek 官方 / 智谱* 三家 provider, 上层 (BE-S2-007
   LangGraph 主循环 / BE-S2-004 招股书入库 / BE-S2-005 混合检索) 不感知厂商差异.
2. 返回结构化 *Result 数据类 (含 usage + cost_cny + provider), 让 BE-S2-007
   写入 ``chat_token_usage`` 表只需一次 ``ChatResult.usage`` 读取.
3. 老 API (``stream_chat`` / 合规护栏) 保持向后兼容: Sprint 1 ``agent_service``
   + ``test_compliance.py`` + e2e ``conftest.fake_llm`` 共 4 处依赖, 不动.

Provider 路由
=============
- ``openai/...``    → 硅基流动 OpenAI 兼容 endpoint (chat / embedding 通用)
- ``deepseek/...``  → DeepSeek 官方 endpoint (仅 chat)
- ``zhipu/...``     → 智谱 endpoint (仅 chat)
- 未匹配           → ``LLMConfigError`` 抛端层, main.py handler 转 503

Rerank 不走 LiteLLM
==================
LiteLLM 1.51 对 cohere-style rerank 的路由要求 ``COHERE_API_KEY`` env, 路径不
适合硅基流动. 改用 ``httpx.AsyncClient`` 直接 POST 到 ``${siliconflow_base_url}
/rerank`` (官方协议是 cohere 兼容的).

成本估算
========
``_PRICE_CNY_PER_M_TOKENS`` 内置硅基流动 / DeepSeek 官方 2025-Q3 公开价 (硬编码常量,
非配置项: 价格单 churn 高, 改起来难免误差, MVP 阶段宁可用近似值也不开口子让运营
偷改). Sprint 3+ 真要做成本看板时再考虑 redis 注入.

合规护栏 (沿用 Sprint 1)
========================
- 输出层走 ``forbidden_pattern_filter`` + ``ensure_disclaimer``
- 关键词黑名单见 .cursor/rules/30 / 50
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, cast

import httpx
import litellm
from litellm import acompletion, aembedding

from app.core.config import Settings, get_settings
from app.core.logging import logger

# ─── 合规护栏 (BE-S5-001: delegate 到 services.compliance v2) ────────────
#
# 历史:
# - Sprint 1 在本模块直接落 6 条 regex + 不真过滤 (扫完 hits 但 cleaned 没 yield 出去)
# - BE-S5-001 v2 把词典 + 否定豁免 + Tier 1/2 分级移到 ``services.compliance`` 模块,
#   本文件仅保留兼容签名让历史调用方不需要改 import.
#
# 新代码请直接 ``from app.services.compliance import forbidden_pattern_filter``,
# 它返 ``(cleaned, ScanResult)``, 比这里的 ``(cleaned, list[str])`` 信息更全.
from app.services.compliance import (
    TIER1_PATTERNS as _TIER1_PATTERNS,
)
from app.services.compliance import (
    TIER2_PATTERNS as _TIER2_PATTERNS,
)
from app.services.compliance import (
    forbidden_pattern_filter as _forbidden_pattern_filter_v2,
)

# 兼容旧 export: 调用方可能在 import ``FORBIDDEN_PATTERNS``, 老的 list 是 6 条 regex,
# 新的拼一起暴露完整词表.
FORBIDDEN_PATTERNS: list[str] = _TIER1_PATTERNS + _TIER2_PATTERNS

DISCLAIMER = "\n\n> ⚠️ 以上分析仅供参考，不构成投资建议，请独立决策。"


def forbidden_pattern_filter(text: str) -> tuple[str, list[str]]:
    """检测违规词. 返回 (清理后文本, 命中的违规词列表) — 兼容 Sprint 1 签名.

    内部走 ``services.compliance.forbidden_pattern_filter`` v2, 它返 ScanResult,
    本函数把 ScanResult.tier1_hits + tier2_hits 合并成单 list 给老调用方用.
    新代码用新模块 (拿到 tier 分级 + negation_skipped 信息).
    """
    cleaned, result = _forbidden_pattern_filter_v2(text)
    return cleaned, result.all_hits


def ensure_disclaimer(text: str) -> str:
    """保证末尾有免责声明 (重复 append 不会双声明)."""
    if "不构成投资建议" in text:
        return text
    return text.rstrip() + DISCLAIMER


# ─── 异常 ────────────────────────────────────────────────────────────────


class LLMError(Exception):
    """所有 LLM 调用相关错误的基类."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.cause = cause


class LLMConfigError(LLMError):
    """密钥 / endpoint / 模型路由配置缺失或非法."""


class LLMProviderError(LLMError):
    """上游 provider 返回错误 / 网络 / 超时."""


# ─── 数据类 (BE-S2-007 落 chat_token_usage 直接读 ChatResult.usage) ────────


_Provider = Literal["siliconflow", "deepseek", "zhipu"]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """LLM token + 成本记账. cost_cny 走 Decimal 防 float 误差."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cny: Decimal

    @classmethod
    def empty(cls) -> TokenUsage:
        return cls(0, 0, 0, Decimal("0"))


@dataclass(frozen=True, slots=True)
class ChatResult:
    """非流 chat 调用的结构化返回. ``tool_calls`` 透传 OpenAI 协议格式."""

    content: str
    finish_reason: str
    usage: TokenUsage
    model: str
    provider: _Provider
    tool_calls: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class ChatStreamChunk:
    """流式 chat 增量 (BE-S2-007 用; 老 stream_chat yield str 不走这条).

    ``delta`` 仅在 token 增量时非空; ``usage`` 仅在最后一帧非空.
    """

    delta: str = ""
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    tool_calls: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """批量 embed 的输出. embeddings[i] 对应输入 texts[i], 长度严格相等."""

    embeddings: list[list[float]]
    usage: TokenUsage
    model: str
    provider: _Provider
    dim: int = 0


@dataclass(frozen=True, slots=True)
class RerankResult:
    """results 已按 score 降序; 每项 (orig_idx, score), orig_idx 指向原 documents 索引."""

    results: list[tuple[int, float]]
    model: str
    provider: _Provider
    usage: TokenUsage = field(default_factory=TokenUsage.empty)


# ─── Provider 路由 + 成本表 ──────────────────────────────────────────────

# 价格单位: CNY / 1M tokens. 数据源: 硅基流动 / DeepSeek 官网 2025-Q3 公开价.
# 改价单: PR + 同步 README §LLM 成本表. CI 暂不强校验真实性.
_PRICE_CNY_PER_M_TOKENS: list[tuple[str, Decimal, Decimal]] = [
    # (substring 匹配, prompt 价格, completion 价格)
    ("DeepSeek-V3", Decimal("1.0"), Decimal("4.0")),
    ("DeepSeek-V2.5", Decimal("1.0"), Decimal("2.0")),
    ("deepseek-chat", Decimal("1.0"), Decimal("2.0")),
    ("deepseek-reasoner", Decimal("4.0"), Decimal("16.0")),
    ("glm-4-9b", Decimal("0.5"), Decimal("0.5")),
    ("glm-4-flash", Decimal("0.0"), Decimal("0.0")),
    ("bge-m3", Decimal("0.5"), Decimal("0.5")),
    ("bge-reranker", Decimal("1.0"), Decimal("1.0")),
]

_COST_QUANT = Decimal("0.000001")  # cost_cny 列 6 位小数


def _estimate_cost_cny(
    model: str, prompt_tokens: int, completion_tokens: int
) -> Decimal:
    """按 ``_PRICE_CNY_PER_M_TOKENS`` 子串匹配估算成本.

    未命中 → 返回 0 + ``logger.warning`` (避免漏价时 ``chat_token_usage.cost_cny``
    NULL 触发 NOT NULL 约束). MVP 阶段允许近似, Sprint 3+ 改成 strict.
    """
    for pattern, p_in, p_out in _PRICE_CNY_PER_M_TOKENS:
        if pattern.lower() in model.lower():
            cost = (
                Decimal(prompt_tokens) * p_in / Decimal("1000000")
                + Decimal(completion_tokens) * p_out / Decimal("1000000")
            )
            return cost.quantize(_COST_QUANT, rounding=ROUND_HALF_UP)
    logger.warning(f"llm.price_unknown model={model} → cost_cny=0")
    return Decimal("0.000000")


def _resolve_provider(model: str) -> _Provider:
    """按 model 前缀路由 provider. 未匹配抛 LLMConfigError."""
    if model.startswith("openai/"):
        return "siliconflow"
    if model.startswith("deepseek/"):
        return "deepseek"
    if model.startswith("zhipu/"):
        return "zhipu"
    raise LLMConfigError(
        f"无法路由模型到 provider: {model!r}. "
        "请用 openai/<...> | deepseek/<...> | zhipu/<...> 前缀.",
        model=model,
    )


def _credentials_for_provider(
    provider: _Provider, settings: Settings
) -> tuple[str, str | None]:
    """返回 (api_key, api_base). api_base 仅 siliconflow 需要 (兼容 OpenAI 协议)."""
    if provider == "siliconflow":
        if not settings.siliconflow_api_key:
            raise LLMConfigError(
                "需要 SILICONFLOW_API_KEY (model 以 openai/ 开头时走硅基流动)",
                provider=provider,
            )
        return settings.siliconflow_api_key, settings.siliconflow_base_url
    if provider == "deepseek":
        if not settings.deepseek_api_key:
            raise LLMConfigError(
                "需要 DEEPSEEK_API_KEY (model 以 deepseek/ 开头)", provider=provider
            )
        return settings.deepseek_api_key, settings.deepseek_base_url
    if provider == "zhipu":
        if not settings.zhipu_api_key:
            raise LLMConfigError(
                "需要 ZHIPU_API_KEY (model 以 zhipu/ 开头)", provider=provider
            )
        # 智谱在 LiteLLM 1.51 没原生 zhipu/ 路由, 走 OpenAI 兼容协议
        # endpoint: paas-v4 兼容 OpenAI Chat Completions
        return settings.zhipu_api_key, "https://open.bigmodel.cn/api/paas/v4"
    raise LLMConfigError(f"unknown provider: {provider}")


def _build_completion_kwargs(
    model: str, settings: Settings
) -> tuple[dict[str, Any], _Provider, str]:
    """构造 LiteLLM acompletion / aembedding 通用 kwargs.

    返回 ``(kwargs, provider, effective_model)``. ``effective_model`` 是真正传给
    LiteLLM 的 model 字符串 — 对外契约 (logs / cost 表 / chat_token_usage 表) 仍用
    原 model (含 ``zhipu/`` 等前缀); 仅 LiteLLM 调用层做 effective_model 重写.

    重写规则:
    - ``zhipu/<x>`` → ``openai/<x>``: LiteLLM 1.51 没原生 ``zhipu/`` 路由, 走智谱
      OpenAI 兼容 endpoint (paas-v4) 调用最稳; provider 仍标 zhipu (用于成本表 +
      chat_token_usage.provider 列).
    - 其他 provider 透传 model 不动.
    """
    provider = _resolve_provider(model)
    api_key, api_base = _credentials_for_provider(provider, settings)
    kwargs: dict[str, Any] = {"api_key": api_key}
    if api_base is not None:
        kwargs["api_base"] = api_base

    effective_model = model
    if provider == "zhipu":
        effective_model = "openai/" + model.removeprefix("zhipu/")

    return kwargs, provider, effective_model


def _configure_litellm_globals() -> None:
    """LiteLLM 全局开关 (drop_params 防 provider 抱怨没用过的字段)."""
    litellm.drop_params = True


_configure_litellm_globals()


# ─── 入口 1: chat (非流) ─────────────────────────────────────────────────


async def chat(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    max_tokens: int = 1500,
    response_format: dict[str, Any] | None = None,
) -> ChatResult:
    """同步 chat (一次性返回完整内容 + tool_calls + usage).

    BE-S2-007 LangGraph 主循环用这个: 决策步要拿全 ``tool_calls`` 才能 dispatch.
    流式播给前端的场景仍走 ``stream_chat`` / ``astream_chat_with_meta``.

    raises:
        LLMConfigError: 模型路由 / 密钥配置缺失
        LLMProviderError: 上游 5xx / 网络 / 超时 / parse 失败
    """
    s = get_settings()
    use_model = model or s.llm_primary_model
    use_temp = temperature if temperature is not None else s.llm_chat_default_temperature

    base_kwargs, provider, effective_model = _build_completion_kwargs(use_model, s)
    call_kwargs: dict[str, Any] = {
        **base_kwargs,
        "model": effective_model,
        "messages": messages,
        "stream": False,
        "temperature": use_temp,
        "max_tokens": max_tokens,
        "timeout": s.llm_request_timeout_seconds,
    }
    if tools:
        call_kwargs["tools"] = tools
    if response_format:
        call_kwargs["response_format"] = response_format

    logger.info(
        f"llm.chat model={use_model} provider={provider} msgs={len(messages)} "
        f"tools={len(tools) if tools else 0}"
    )

    try:
        resp = await acompletion(**call_kwargs)
    except Exception as e:
        logger.error(f"llm.chat error model={use_model}: {type(e).__name__}: {e}")
        raise LLMProviderError(
            f"chat call failed: {e}", provider=provider, model=use_model, cause=e
        ) from e

    return _parse_chat_response(resp, use_model, provider)


def _parse_chat_response(
    resp: Any, model: str, provider: _Provider
) -> ChatResult:
    """从 LiteLLM ``ModelResponse`` 抽出我们关心的字段."""
    try:
        choice = resp.choices[0]
        msg = choice.message
        content = (msg.content or "") if msg is not None else ""
        finish_reason = choice.finish_reason or "stop"
        raw_tool_calls = getattr(msg, "tool_calls", None)
    except (AttributeError, IndexError) as e:
        raise LLMProviderError(
            f"unexpected chat response shape: {e}",
            provider=provider,
            model=model,
            cause=e,
        ) from e

    tool_calls: list[dict[str, Any]] | None = None
    if raw_tool_calls:
        tool_calls = [_normalize_tool_call(tc) for tc in raw_tool_calls]

    usage_obj = getattr(resp, "usage", None)
    prompt_t = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
    completion_t = int(getattr(usage_obj, "completion_tokens", 0) or 0)
    total_t = int(getattr(usage_obj, "total_tokens", prompt_t + completion_t) or 0)
    cost = _estimate_cost_cny(model, prompt_t, completion_t)

    return ChatResult(
        content=content,
        finish_reason=finish_reason,
        usage=TokenUsage(prompt_t, completion_t, total_t, cost),
        model=model,
        provider=provider,
        tool_calls=tool_calls,
    )


def _normalize_tool_call(tc: Any) -> dict[str, Any]:
    """把 LiteLLM 的 ``tool_call`` 对象 / dict 都规整成统一 dict.

    LiteLLM 对不同 provider 返回类型不一: openai-compat 返回 pydantic,
    某些代理返回 plain dict. 统一为::

        {"id": "call_abc", "type": "function",
         "function": {"name": "...", "arguments": "..."}}
    """
    if isinstance(tc, dict):
        return tc
    fn = getattr(tc, "function", None)
    return {
        "id": getattr(tc, "id", ""),
        "type": getattr(tc, "type", "function"),
        "function": {
            "name": getattr(fn, "name", "") if fn else "",
            "arguments": getattr(fn, "arguments", "") if fn else "",
        },
    }


# ─── 入口 2: stream_chat (兼容 + 增强) ────────────────────────────────────


async def stream_chat(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1500,
) -> AsyncIterator[str]:
    """流式输出 token (Sprint 1 兼容契约: yield str).

    保留:
    - 末尾自动追加免责声明 (与 Sprint 1 一致)
    - 没配 LLM key 时返回友好引导文 (而非抛错)
    - 上游异常时 yield ``⚠️ 模型调用失败...`` 而不抛 (SSE 不能 break)

    BE-S2-007 LangGraph 不用这个, 用 ``astream_chat_with_meta`` 拿到 usage / tool_calls.
    """
    s = get_settings()
    use_model = model or s.llm_primary_model

    if not s.has_llm_credential:
        yield (
            "⚠️ 后端尚未配置 LLM API Key。\n\n"
            "请在 `apps/api/.env` 中填入 `SILICONFLOW_API_KEY` 或 "
            "`DEEPSEEK_API_KEY`，然后重启服务。\n\n"
            "硅基流动注册（推荐）：https://siliconflow.cn"
        )
        return

    logger.info(f"llm.stream_chat model={use_model} msgs={len(messages)}")

    # BE-S5-001: chunk 实时合规扫描.
    # 策略: 每收一个 chunk 拼到 ``buffer`` (累计 full), 在 ``buffer`` 末尾
    # ``tail_buffer_chars`` 范围内做扫描, 因为 LLM 可能把 "必涨" 切成 ["必", "涨"]
    # 两个 chunk; 只对"已确定不再增长的部分" yield 给上游, 让 chunk 边界拆词
    # 也能被本扫描捕获.
    # Tier 1 命中: yield 命中位置之前的干净前缀 + 阻断提示, 截断后续所有 LLM 输出.
    # Tier 2 命中: 让它过去 (替换为 [已脱敏]) + 末尾再次 logger.warning 上报 metrics.
    from app.services.compliance import (
        find_first_tier1_hit,
    )
    from app.services.compliance import (
        forbidden_pattern_filter as _scan_v2,
    )

    tail_buffer_chars = 16  # 比最长 Tier 1 / Tier 2 词长一点, 足够覆盖跨 chunk 拼接
    block_notice = (
        "\n\n> ⚠️ 检测到不合规内容，已停止输出。请尝试调整提问或换一种表达。"
    )

    full_buffer = ""
    flushed_len = 0  # 已 yield 给上游的 full_buffer 前缀长度
    blocked = False

    try:
        base_kwargs, _provider, effective_model = _build_completion_kwargs(use_model, s)
        call_kwargs: dict[str, Any] = {
            **base_kwargs,
            "model": effective_model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # 与 astream_chat_with_meta 对齐 — 流式调用必须显式 timeout 防 hang
            "timeout": s.llm_request_timeout_seconds,
        }
        response = await acompletion(**call_kwargs)
        async for chunk in response:
            try:
                delta = chunk.choices[0].delta.content or ""
            except (AttributeError, IndexError):
                delta = ""
            if not delta:
                continue
            full_buffer += delta

            # 决定本轮可 flush 的安全边界: 末尾留 tail_buffer_chars 不 yield
            # (可能正在被切红线词中间), 前面的部分扫完 + yield.
            safe_until = max(flushed_len, len(full_buffer) - tail_buffer_chars)
            if safe_until <= flushed_len:
                # 还不到 16 字, 全压在 buffer 里, 暂不 yield
                continue
            pending = full_buffer[flushed_len:safe_until]

            hit = find_first_tier1_hit(pending)
            if hit is not None:
                hit_start, hit_word = hit
                clean_prefix, prefix_scan = _scan_v2(pending[:hit_start])
                logger.warning(
                    f"llm.stream_chat.tier1_blocked hit={hit_word!r} pos={hit_start}"
                )
                if clean_prefix:
                    yield clean_prefix
                yield block_notice
                blocked = True
                break

            # 干净部分(可能含 Tier 2): 过滤后 yield, hits 上报
            cleaned, scan_res = _scan_v2(pending)
            if scan_res.has_tier2:
                logger.warning(
                    f"llm.stream_chat.tier2_redacted hits={scan_res.tier2_hits}"
                )
            yield cleaned
            flushed_len = safe_until

        # 主循环结束: 把 tail buffer 剩余的部分 flush 出去
        if not blocked:
            tail = full_buffer[flushed_len:]
            if tail:
                hit = find_first_tier1_hit(tail)
                if hit is not None:
                    hit_start, hit_word = hit
                    clean_prefix, _ = _scan_v2(tail[:hit_start])
                    logger.warning(
                        f"llm.stream_chat.tier1_blocked_tail hit={hit_word!r}"
                    )
                    if clean_prefix:
                        yield clean_prefix
                    yield block_notice
                    blocked = True
                else:
                    cleaned, scan_res = _scan_v2(tail)
                    if scan_res.has_tier2:
                        logger.warning(
                            f"llm.stream_chat.tier2_redacted_tail hits={scan_res.tier2_hits}"
                        )
                    yield cleaned

    except LLMConfigError as e:
        # 配置错误 (没填 key) 走友好引导, 不让 SSE 断
        logger.error(f"llm.stream_chat config error: {e}")
        yield f"\n\n⚠️ 模型配置错误: {e}"
        return
    except Exception as e:
        logger.error(f"llm.stream_chat error: {e}")
        yield f"\n\n⚠️ 模型调用失败: {type(e).__name__}: {e}"
        return

    if blocked:
        return

    if "不构成投资建议" not in full_buffer:
        yield DISCLAIMER


async def astream_chat_with_meta(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    max_tokens: int = 1500,
) -> AsyncIterator[ChatStreamChunk]:
    """流式 chat (BE-S2-007 用): yield ``ChatStreamChunk``.

    与 ``stream_chat`` 区别:
    - 拿到 finish_reason / usage / tool_calls (在最后一帧)
    - 不自动追加 disclaimer (端层 BE-S2-007 自行兜底, 见 spec/04 §5)
    - 配置 / 上游错误直接 raise (不 swallow), 让主循环决定 retry / 降级

    raises: LLMConfigError / LLMProviderError
    """
    s = get_settings()
    use_model = model or s.llm_primary_model
    use_temp = temperature if temperature is not None else s.llm_chat_default_temperature

    base_kwargs, provider, effective_model = _build_completion_kwargs(use_model, s)
    call_kwargs: dict[str, Any] = {
        **base_kwargs,
        "model": effective_model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},  # OpenAI / 硅基均支持
        "temperature": use_temp,
        "max_tokens": max_tokens,
        # 流式必须显式 timeout — LiteLLM 把它落到 httpx transport, 既覆盖
        # connect, 也覆盖 chunk 间 read. 不设的话 provider 卡住不返回 chunk
        # (实测 GLM-4-Flash 第二轮 tool_result 调用时偶发) 会让 SSE 永远 hang,
        # 前端只能看见"加载中"无尽转圈. 60s 是兜底, 单步 LLM 正常流应远低于它.
        "timeout": s.llm_request_timeout_seconds,
    }
    if tools:
        call_kwargs["tools"] = tools

    logger.info(
        f"llm.stream_chat_meta model={use_model} provider={provider} "
        f"msgs={len(messages)} tools={len(tools) if tools else 0} "
        f"timeout={s.llm_request_timeout_seconds}s"
    )

    final_finish: str | None = None
    final_usage: TokenUsage | None = None
    final_tool_calls: list[dict[str, Any]] | None = None
    tool_call_acc: dict[int, dict[str, Any]] = {}

    # 双层 timeout 防御:
    #
    # 1) ``call_kwargs["timeout"]`` 走 LiteLLM → httpx transport, 是"应该"工作的总
    #    timeout, 但实测 LiteLLM 1.51 在 stream=True + 智谱 paas-v4 OpenAI 兼容路径
    #    下不会把它真正落到 httpx 的 read timeout, provider 卡住不发 chunk 时
    #    ``async for chunk in response`` 会永远等下去 (踩坑 21 第二次复现).
    #
    # 2) 这里加 ``asyncio.wait_for`` per-chunk 超时, 是不依赖 LiteLLM 的硬兜底:
    #    每次等 chunk 超过 ``llm_chunk_idle_timeout_seconds`` (默认 ~30s) 就抛
    #    ``TimeoutError`` → 转成 ``LLMProviderError``. 即便 LiteLLM /
    #    httpx / provider 任意一层不遵守 timeout 协议, 这里也兜得住.
    #
    # 取舍: per-chunk 而不是总 timeout 是因为 LLM 流可能正常持续 1-3 分钟 (长答),
    # 总 timeout 60s 会把成功的长流也误杀; 而单 chunk 间 30s 没新字必定是 hang.
    chunk_idle_timeout = float(s.llm_request_timeout_seconds) / 2  # 60s/2 = 30s

    try:
        response = await asyncio.wait_for(
            acompletion(**call_kwargs),
            timeout=s.llm_request_timeout_seconds,
        )
        # response 拿到后, 流的迭代每次最多等 chunk_idle_timeout 没新内容就放弃
        response_iter = response.__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(
                    response_iter.__anext__(),
                    timeout=chunk_idle_timeout,
                )
            except StopAsyncIteration:
                break
            delta_text = ""
            try:
                choice = chunk.choices[0]
                delta_obj = choice.delta
                delta_text = (getattr(delta_obj, "content", None) or "")
                if choice.finish_reason:
                    final_finish = choice.finish_reason
                # tool_calls 在流式增量里是分片的, 按 index 累积 args 字符串
                tc_chunks = getattr(delta_obj, "tool_calls", None)
                if tc_chunks:
                    for tc in tc_chunks:
                        idx = getattr(tc, "index", 0)
                        slot = tool_call_acc.setdefault(
                            idx,
                            {"id": "", "type": "function",
                             "function": {"name": "", "arguments": ""}},
                        )
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                slot["function"]["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                slot["function"]["arguments"] += fn.arguments
            except (AttributeError, IndexError):
                pass

            usage_obj = getattr(chunk, "usage", None)
            if usage_obj is not None:
                p_t = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
                c_t = int(getattr(usage_obj, "completion_tokens", 0) or 0)
                t_t = int(getattr(usage_obj, "total_tokens", p_t + c_t) or 0)
                final_usage = TokenUsage(
                    p_t, c_t, t_t, _estimate_cost_cny(use_model, p_t, c_t)
                )

            if delta_text:
                yield ChatStreamChunk(delta=delta_text)
    except TimeoutError as e:
        logger.error(
            f"llm.stream_chat_meta TIMEOUT model={use_model} "
            f"chunk_idle={chunk_idle_timeout}s — provider 没在限时内返回新 chunk, "
            f"可能是 GLM-4-Flash tool_result 路径偶发卡死 (踩坑 21)"
        )
        raise LLMProviderError(
            f"stream_chat 超时 ({chunk_idle_timeout}s 内无新 chunk)",
            provider=provider,
            model=use_model,
            cause=e,
        ) from e
    except LLMError:
        raise
    except Exception as e:
        logger.error(f"llm.stream_chat_meta error model={use_model}: {e}")
        raise LLMProviderError(
            f"stream_chat call failed: {e}",
            provider=provider,
            model=use_model,
            cause=e,
        ) from e

    if tool_call_acc:
        final_tool_calls = [tool_call_acc[k] for k in sorted(tool_call_acc)]

    yield ChatStreamChunk(
        finish_reason=final_finish or "stop",
        usage=final_usage,
        tool_calls=final_tool_calls,
    )


# ─── 入口 3: embed (批量, 自动分批) ──────────────────────────────────────


async def embed(
    texts: list[str],
    *,
    model: str | None = None,
    batch_size: int | None = None,
) -> EmbeddingResult:
    """批量 embedding. 输出维度自动跟随 model (默认 bge-m3 → 1024).

    自动按 ``batch_size`` (默认 settings.llm_embedding_batch_size, 当前 32) 分批,
    输入超过 32 时透明合并; usage 累加, embeddings 顺序与输入对齐.

    raises: LLMConfigError / LLMProviderError / ValueError (空输入)
    """
    if not texts:
        raise ValueError("embed: texts 不能为空")

    s = get_settings()
    use_model = model or s.llm_embedding_model
    use_bsz = batch_size or s.llm_embedding_batch_size
    if use_bsz <= 0:
        raise ValueError(f"batch_size 必须 > 0, got {use_bsz}")

    base_kwargs, provider, effective_model = _build_completion_kwargs(use_model, s)

    all_embeddings: list[list[float]] = []
    total_p = total_c = total_t = 0
    total_cost = Decimal("0")

    for start in range(0, len(texts), use_bsz):
        chunk = texts[start : start + use_bsz]
        try:
            resp = await aembedding(
                **base_kwargs,
                model=effective_model,
                input=chunk,
                timeout=s.llm_request_timeout_seconds,
            )
        except Exception as e:
            logger.error(f"llm.embed error model={use_model}: {e}")
            raise LLMProviderError(
                f"embedding call failed: {e}",
                provider=provider,
                model=use_model,
                cause=e,
            ) from e

        try:
            data = resp.data if hasattr(resp, "data") else resp["data"]
        except (AttributeError, KeyError, TypeError) as e:
            raise LLMProviderError(
                f"unexpected embedding response: {e}",
                provider=provider,
                model=use_model,
                cause=e,
            ) from e

        for item in data:
            emb = item["embedding"] if isinstance(item, dict) else item.embedding
            all_embeddings.append(list(emb))

        u = getattr(resp, "usage", None)
        if u is not None:
            p = int(getattr(u, "prompt_tokens", 0) or 0)
            t = int(getattr(u, "total_tokens", p) or 0)
            total_p += p
            total_t += t
            total_cost += _estimate_cost_cny(use_model, p, 0)

    if len(all_embeddings) != len(texts):
        raise LLMProviderError(
            f"embedding 数量不匹配: 输入 {len(texts)} 输出 {len(all_embeddings)}",
            provider=provider,
            model=use_model,
        )

    dim = len(all_embeddings[0]) if all_embeddings else 0
    return EmbeddingResult(
        embeddings=all_embeddings,
        usage=TokenUsage(
            total_p, total_c, total_t or total_p, total_cost.quantize(_COST_QUANT)
        ),
        model=use_model,
        provider=provider,
        dim=dim,
    )


# ─── 入口 4: rerank (绕过 LiteLLM 直接 httpx) ─────────────────────────────


async def rerank(
    query: str,
    documents: list[str],
    *,
    model: str | None = None,
    top_n: int | None = None,
) -> RerankResult:
    """走硅基流动 ``/v1/rerank`` (cohere 兼容). 返回按 score 降序的 (orig_idx, score).

    LiteLLM 的 ``arerank`` 路由只走 cohere 官方; 硅基流动有同 schema 但要 base_url
    覆盖, 直接 httpx 更直接也省配置. 后续 Sprint 3+ 用 LiteLLM 时可再切换.

    raises: LLMConfigError / LLMProviderError / ValueError
    """
    if not documents:
        raise ValueError("rerank: documents 不能为空")
    if not query.strip():
        raise ValueError("rerank: query 不能为空")

    s = get_settings()
    use_model = model or s.llm_rerank_model
    if not s.siliconflow_api_key:
        raise LLMConfigError(
            "rerank 当前仅支持硅基流动, 需要 SILICONFLOW_API_KEY",
            provider="siliconflow",
            model=use_model,
        )

    url = s.siliconflow_base_url.rstrip("/") + "/rerank"
    payload: dict[str, Any] = {
        "model": use_model,
        "query": query,
        "documents": documents,
        "return_documents": False,
    }
    if top_n is not None:
        payload["top_n"] = top_n

    logger.info(
        f"llm.rerank model={use_model} q_len={len(query)} docs={len(documents)} "
        f"top_n={top_n}"
    )

    try:
        async with httpx.AsyncClient(
            timeout=s.llm_request_timeout_seconds
        ) as http:
            resp = await http.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {s.siliconflow_api_key}"},
            )
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            f"llm.rerank http_error model={use_model} status={e.response.status_code}"
        )
        raise LLMProviderError(
            f"rerank HTTP {e.response.status_code}: {e.response.text[:200]}",
            provider="siliconflow",
            model=use_model,
            cause=e,
        ) from e
    except (httpx.RequestError, ValueError) as e:
        logger.error(f"llm.rerank error model={use_model}: {e}")
        raise LLMProviderError(
            f"rerank failed: {e}", provider="siliconflow", model=use_model, cause=e
        ) from e

    raw_results = body.get("results", [])
    parsed: list[tuple[int, float]] = []
    for r in raw_results:
        try:
            parsed.append((int(r["index"]), float(r["relevance_score"])))
        except (KeyError, TypeError, ValueError) as e:
            raise LLMProviderError(
                f"rerank malformed result item: {r}",
                provider="siliconflow",
                model=use_model,
                cause=e,
            ) from e

    parsed.sort(key=lambda x: x[1], reverse=True)

    meta = body.get("meta") or {}
    billed = (
        cast(dict[str, Any], meta.get("billed_units", {}) or {})
        if isinstance(meta, dict)
        else {}
    )
    p_t = int(billed.get("input_tokens", 0) or 0)
    cost = _estimate_cost_cny(use_model, p_t, 0)

    return RerankResult(
        results=parsed,
        model=use_model,
        provider="siliconflow",
        usage=TokenUsage(p_t, 0, p_t, cost),
    )


__all__ = [
    "DISCLAIMER",
    "FORBIDDEN_PATTERNS",
    "ChatResult",
    "ChatStreamChunk",
    "EmbeddingResult",
    "LLMConfigError",
    "LLMError",
    "LLMProviderError",
    "RerankResult",
    "TokenUsage",
    "astream_chat_with_meta",
    "chat",
    "embed",
    "ensure_disclaimer",
    "forbidden_pattern_filter",
    "rerank",
    "stream_chat",
]
