"""``get_sentiment_summary`` Tool — 情感分布占位 (BE-S2-006b).

对应 spec/04 §3.1 第 4 个 Tool. 当前 Sprint 2 阶段**没有真实的全网文章源**:
- spec/04 §4.1 列的搜狗微信搜索 / 雪球 / 智通财经 / 财联社 这些数据源都属于
  Sprint 3 范围 (BE-S3-xxx 文章聚合 + 情感打标), 当前 ``articles`` 表也还没建
- spec/04 §3.4 评测体系也提到"情感模块只是 P1, 不阻塞 MVP"

为什么仍然在 Sprint 2 占位实现这个 Tool
========================================
1. **保护 LLM 工具集合形状稳定**: BE-S2-007 LangGraph 主循环里 system prompt 会
   告诉 LLM "你有 5 个工具", 真有用户问"市场情绪如何"时 LLM 会去 call;
   如果 Tool 不存在 LLM 会幻觉一个工具名出来. 占位实现让"工具存在 + 数据缺失"
   path 显式可控
2. **统一数据缺失模板**: 与 ``get_financial_statements`` 同款 — ``ok=True`` +
   ``warning`` (而非 ``ok=False``), 让 LLM 知道是"数据源未接入" 而非"调用失败",
   选择走 ``hybrid_search`` 在招股书"风险因素 / 行业前景"章节兜底, 或显式回答
   "暂无足够数据"
3. **接入真实文章源时只换实现, 不动 schema**: BE-S3-xxx 在不动 ``input_model``
   / ``data`` 字段名的前提下, 把 ``ok=True/false`` 与 ``counts`` / ``top_articles``
   填上真值即可

返回结构 (与 spec/04 §3.1 对齐 + 留足 BE-S3 扩展位)
====================================================
```python
{
    "code": "0700.HK",
    "window_days": 7,
    "counts": {"positive": 0, "neutral": 0, "negative": 0},
    "top_articles": [],
    "data_source_status": "not_connected",
    "warning": "情感数据源 (...) 尚未接入 ...",
}
```

不在本 Tool 做
==============
- 微信公众号 / 雪球 / 智通财经 抓取 (Sprint 3 BE-S3-xxx)
- 情感 LLM 打标 (Sprint 3 同 PR)
- 走 hybrid_search 在招股书原文里抽情感关键词 (LLM 自己会判断要不要在主循环里
  追问 hybrid_search; 不在本 Tool 内 fan-out)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.services.agent.sandbox import sandboxed
from app.services.agent.tool_registry import Tool, ToolResult, register

_TOOL_NAME = "get_sentiment_summary"
_TOOL_DESCRIPTION = (
    "获取该新股近 N 天全网文章情感分布（正向 / 中性 / 负向 占比 + Top 文章）。"
    "**当前为 placeholder 实现**：文章聚合数据源（搜狗微信 / 雪球 / 智通财经）"
    "尚未接入，返回值始终是 ``data_source_status='not_connected'`` + 全 0 计数 + warning。"
    "若需要市场观点，请改用 hybrid_search 在招股书原文中搜索 '风险因素' / '行业前景' 章节。"
)
_TOOL_TIMEOUT = 3.0
_DEFAULT_WINDOW_DAYS = 7
_MAX_WINDOW_DAYS = 30


class GetSentimentSummaryInput(BaseModel):
    """``get_sentiment_summary`` 入参. ``window_days`` 留给 BE-S3 真接入时按时间窗
    截取文章; 当前占位实现仅作 metadata 透传给 LLM.
    """

    code: str = Field(
        min_length=4,
        max_length=16,
        description="新股代码（带市场后缀），如 ``0700.HK`` / ``600519.SH``。",
    )
    window_days: int = Field(
        default=_DEFAULT_WINDOW_DAYS,
        ge=1,
        le=_MAX_WINDOW_DAYS,
        description=f"时间窗 (近 N 天), 默认 {_DEFAULT_WINDOW_DAYS} 天, 最多 {_MAX_WINDOW_DAYS} 天。",
    )


@sandboxed(input_model=GetSentimentSummaryInput, timeout_seconds=_TOOL_TIMEOUT)
async def _run(args: GetSentimentSummaryInput) -> ToolResult:
    """占位实现: 直接返回固定空结构 + warning, 不做任何 IO."""
    data: dict[str, Any] = {
        "code": args.code.upper().strip(),
        "window_days": args.window_days,
        "counts": {"positive": 0, "neutral": 0, "negative": 0},
        "top_articles": [],
        "data_source_status": "not_connected",
        "warning": (
            "情感数据源（搜狗微信 / 雪球 / 智通财经 / 财联社等）尚未接入；"
            "本 Tool 当前是占位实现，返回的 counts 与 top_articles 不代表真实数据。"
            "建议改用 hybrid_search 在招股书原文中检索 '风险因素' / '行业前景' / "
            "'竞争格局' 章节，或基于 get_ipo_basic_info / get_financial_statements 给出客观分析。"
        ),
    }
    return ToolResult.success(data)


register(
    Tool(
        name=_TOOL_NAME,
        description=_TOOL_DESCRIPTION,
        input_model=GetSentimentSummaryInput,
        runner=_run,
        timeout_seconds=_TOOL_TIMEOUT,
        tags=("ipo", "sentiment", "placeholder"),
    )
)


__all__ = ["GetSentimentSummaryInput"]
