"""AI Agent 请求/响应模型."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DiagnoseRequest(BaseModel):
    code: str = Field(min_length=1, max_length=16, description="新股代码, 如 0700.HK / 600519")
    name: str | None = Field(default=None, max_length=64)
    question: str | None = Field(
        default=None,
        max_length=500,
        description="可选, 用户具体问题; 留空则给出基础诊断",
    )


class AgentChunk(BaseModel):
    """SSE 流式输出的单个事件 payload."""

    type: Literal["start", "delta", "end", "error", "meta"]
    content: str = ""
    metadata: dict[str, str | int | float | None] | None = None
