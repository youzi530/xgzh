"""Chat / Agent 请求响应 schema (BE-S2-007).

这一组 schema 是 ``POST /v1/chat/diagnose`` SSE 端层的对外契约:

入参 (POST body)
================
``ChatDiagnoseRequest``
- ``question`` 用户问 (必填)
- ``ipo_code`` 锚定哪只 IPO (可选, null = 通用对话)
- ``session_id`` 续聊会话 ID (可选, null = 起新会话)
- ``model`` 指定 LLM (可选, 走 settings.llm_primary_model 默认)

出参 (SSE 事件流)
=================
- ``ChatStartPayload`` event=start: ``{session_id, ipo, model}``
- ``ChatDeltaPayload`` event=delta: ``{content}`` 增量 token
- ``ChatToolCallPayload`` event=tool_call: ``{name, args, status, latency_ms,
  result_preview?}`` 工具调用透传 (FE 用来画"分析中…"步骤条)
- ``ChatSourcesPayload`` event=sources: ``{citations: [...]}`` 引用源 (LLM 写完后一次性下发)
- ``ChatEndPayload`` event=end: ``{message_id, usage, finish_reason}``
- ``ChatErrorPayload`` event=error: ``{message}`` 端层兜底

老 ``app/schemas/agent.py::DiagnoseRequest`` 不删 (Sprint 1 老路径
``/v1/agent/diagnose`` 还在用), Sprint 3 时再砍.

约束 / 取舍
===========
- 字段命名贴 OpenAI Chat Completion 协议 (``finish_reason`` / ``usage`` /
  ``tool_calls``), 让 FE 写 SSE consume 时心智一致, Pinia store 直接缓存
- 所有数值字段都标注单位/口径 (cost_cny / latency_ms / tokens), 防 FE 误解
- ``citations`` 复用 ``services/agent/citation.py::Citation`` dict 形态;
  这里再用 pydantic 重新声明一遍是为了 OpenAPI schema 自动化, 不引入运行期
  校验开销 (端层手动 ``c.to_dict()`` 后 dict 直接走 SSE)
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChatDiagnoseRequest(BaseModel):
    """``POST /v1/chat/diagnose`` 入参."""

    question: str = Field(min_length=1, max_length=2000, description="用户问题")
    ipo_code: str | None = Field(
        default=None,
        max_length=16,
        description="锚定的 IPO 代码 (如 0700.HK / 600519.SH); null = 通用对话",
    )
    session_id: UUID | None = Field(
        default=None, description="续聊会话 id; null = 起新会话"
    )
    model: str | None = Field(
        default=None, max_length=64, description="可选, 指定 LLM 模型 (走 LiteLLM 路由)"
    )
    max_steps: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="ReAct 最大步数; 默认走 settings.agent_max_steps",
    )


# ─── SSE 事件 payload ─────────────────────────────────────────────────────


class ChatCitation(BaseModel):
    """SSE sources 事件单条引用 (与 services/agent/citation.py::Citation 对齐)."""

    idx: int
    chunk_id: str
    doc_id: str
    ipo_code: str | None = None
    page: int | None = None
    snippet: str
    score: float


class ChatTokenUsageDTO(BaseModel):
    """SSE end 事件携带的 token usage (聚合主循环所有 LLM 调用)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_cny: float = 0.0
    llm_call_count: int = 0


class ChatStartPayload(BaseModel):
    session_id: str
    ipo_code: str | None = None
    model: str


class ChatDeltaPayload(BaseModel):
    content: str


class ChatToolCallPayload(BaseModel):
    """单个 tool 调用从入参 → 结果的全过程 (status='ok'/'error'/'timeout' 时下发)."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    args: dict[str, Any] | None = None
    status: Literal["ok", "error", "timeout"]
    latency_ms: int = 0
    error: str | None = None
    result_preview: dict[str, Any] | None = Field(
        default=None,
        description="ok 时 ToolResult.data 摘要 (前若干键); 防 SSE 把整张表都吐",
    )


class ChatSourcesPayload(BaseModel):
    citations: list[ChatCitation] = Field(default_factory=list)


class ChatEndPayload(BaseModel):
    message_id: str
    finish_reason: str
    usage: ChatTokenUsageDTO
    invalid_citation_indices: list[int] = Field(
        default_factory=list,
        description="LLM 引用了不存在的 [N], 已 strip; 端层 / 运营关注",
    )


class ChatErrorPayload(BaseModel):
    message: str


class ChatQuotaPayload(BaseModel):
    """配额状态 (BE-S2-008). 用于 429 响应 body + Sprint 3 起 SSE end 事件携带.

    与 ``services/agent/quota.py::QuotaStatus.to_dict()`` 一一对齐, 便于
    端层 ``status.to_dict()`` 直接 ``model_validate(...)``.
    """

    plan: Literal["free", "vip", "anonymous"]
    limit: int = Field(description="-1 = 无限")
    used: int
    remaining: int = Field(description="-1 = 无限")
    window_seconds: int = Field(description="滑动窗口长度 (秒)")
    retry_after_seconds: int | None = Field(
        default=None,
        description="超额时建议等待的秒数; None = 还有余额或 VIP 无限",
    )


class ChatQuotaExceededResponse(BaseModel):
    """``POST /v1/chat/diagnose`` 超配额时的 HTTP 429 body."""

    code: Literal["agent_quota_exceeded"] = "agent_quota_exceeded"
    message: str = Field(description="人话提示, FE 默认 toast")
    quota: ChatQuotaPayload


__all__ = [
    "ChatCitation",
    "ChatDeltaPayload",
    "ChatDiagnoseRequest",
    "ChatEndPayload",
    "ChatErrorPayload",
    "ChatQuotaExceededResponse",
    "ChatQuotaPayload",
    "ChatSourcesPayload",
    "ChatStartPayload",
    "ChatTokenUsageDTO",
    "ChatToolCallPayload",
]
