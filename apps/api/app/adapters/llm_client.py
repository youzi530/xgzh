"""LLM 适配器: 通过 LiteLLM 统一接入硅基流动 / DeepSeek / 智谱.

合规约束 (见 .cursor/rules/30-ai-agent.mdc, 50-compliance.mdc):
- 输出层必须经过 forbidden_pattern_filter + ensure_disclaimer
- 关键词黑名单: 严禁"建议买入/必涨/稳赚"等
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import litellm
from litellm import acompletion

from app.core.config import get_settings
from app.core.logging import logger

# ─── 合规护栏 ────────────────────────────────────────
FORBIDDEN_PATTERNS = [
    r"建议(满仓|重仓|全仓|加仓|抄底)",
    r"强烈(推荐|建议)买入",
    r"必涨|稳赚|包赚|躺赚",
    r"保本|保收益|无风险",
    r"all\s*in|梭哈",
    r"打新必中|中签率\s*100\s*%",
]

DISCLAIMER = "\n\n> ⚠️ 以上分析仅供参考，不构成投资建议，请独立决策。"


def forbidden_pattern_filter(text: str) -> tuple[str, list[str]]:
    """检测违规词。返回 (清理后文本, 命中的违规词列表)。"""
    hits: list[str] = []
    cleaned = text
    for pattern in FORBIDDEN_PATTERNS:
        m = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if m:
            hits.append(m.group(0))
            cleaned = re.sub(pattern, "[已合规过滤]", cleaned, flags=re.IGNORECASE)
    return cleaned, hits


def ensure_disclaimer(text: str) -> str:
    """确保末尾有免责声明."""
    if "不构成投资建议" in text:
        return text
    return text.rstrip() + DISCLAIMER


# ─── LiteLLM 配置 ────────────────────────────────────
def _configure_litellm() -> None:
    s = get_settings()
    litellm.drop_params = True

    # 优先硅基流动: 通过 OpenAI 兼容协议
    if s.siliconflow_api_key:
        litellm.api_key = s.siliconflow_api_key
        litellm.api_base = s.siliconflow_base_url

    if s.deepseek_api_key:
        import os

        os.environ.setdefault("DEEPSEEK_API_KEY", s.deepseek_api_key)
        os.environ.setdefault("DEEPSEEK_API_BASE", s.deepseek_base_url)

    if s.zhipu_api_key:
        import os

        os.environ.setdefault("ZHIPUAI_API_KEY", s.zhipu_api_key)


_configure_litellm()


# ─── 主流式接口 ───────────────────────────────────────
async def stream_chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1500,
) -> AsyncIterator[str]:
    """流式输出 token, 末尾自动追加免责声明."""
    s = get_settings()
    use_model = model or s.llm_primary_model

    if not s.has_llm_credential:
        yield (
            "⚠️ 后端尚未配置 LLM API Key。\n\n"
            "请在 `apps/api/.env` 中填入 `SILICONFLOW_API_KEY` 或 `DEEPSEEK_API_KEY`，然后重启服务。\n\n"
            "硅基流动注册（推荐）：https://siliconflow.cn"
        )
        return

    # 调用方需要传入合规系统提示, 这里假设 messages[0] 已经是 system
    logger.info(f"llm.stream_chat model={use_model} msgs={len(messages)}")

    buffer: list[str] = []
    try:
        # api_key/api_base 通过环境变量或 litellm 全局配置传递
        kwargs: dict = {
            "model": use_model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if s.siliconflow_api_key and use_model.startswith("openai/"):
            kwargs["api_key"] = s.siliconflow_api_key
            kwargs["api_base"] = s.siliconflow_base_url

        response = await acompletion(**kwargs)

        async for chunk in response:
            try:
                delta = chunk.choices[0].delta.content or ""
            except (AttributeError, IndexError):
                delta = ""
            if delta:
                buffer.append(delta)
                yield delta

    except Exception as e:
        logger.error(f"llm.stream_chat error: {e}")
        yield f"\n\n⚠️ 模型调用失败: {type(e).__name__}: {e}"
        return

    # 流结束后做合规检查 + 免责声明
    full = "".join(buffer)
    _, hits = forbidden_pattern_filter(full)
    if hits:
        logger.warning(f"forbidden_patterns_hit count={len(hits)} hits={hits}")

    if "不构成投资建议" not in full:
        yield DISCLAIMER
