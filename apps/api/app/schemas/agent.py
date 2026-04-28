"""AI Agent 请求/响应模型."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ipo import Market


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


# ─── Sprint 4 BE-S4-004: AI 历史规律分析 SSE ─────────────────────────


class HistoricalPatternRequest(BaseModel):
    """AI 历史规律分析请求 (POST /agent/historical-pattern SSE).

    走 DeepSeek-R1 思维链推理 (fallback GLM-4-Flash); 输出 5 段结构化报告 +
    引用源. ``current_ipo_code`` 可选: 给"当前 IPO 在分布中的位置"参考.
    """

    industry: str = Field(
        min_length=1, max_length=32, description="一级行业 (如 '互联网' / '医药')"
    )
    market: Market | None = Field(
        default=None, description="市场: HK / A; 不传则全市场"
    )
    year_from: int = Field(default=2022, ge=1990, le=2100, description="起始年份")
    year_to: int = Field(default=2025, ge=1990, le=2100, description="结束年份")
    current_ipo_code: str | None = Field(
        default=None,
        max_length=16,
        description="可选; 给'当前 IPO 在分布中的位置'参考",
    )
