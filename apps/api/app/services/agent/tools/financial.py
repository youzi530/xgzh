"""``get_financial_statements`` Tool — 财务摘要 (BE-S2-006a).

对应 spec/04 §3.1 第 2 个 Tool. 当前阶段没接 AKShare / Futu 财务接口, 数据
源是 ``ipos.extra.financial_summary`` JSONB (由 BE-S2-004 招股书 RAG ingest 时
写入 + 运营手动补). 接口契约对齐 spec, 让 BE-S2-007 LangGraph 主循环里 LLM 走
ReAct 时 description 与官方 spec 完全一致, 后续接入真实数据源时**只换实现, 不
改 schema**.

数据来源
========
1. ``ipo_service.get_ipo_detail(code)``: BE-009 已落, 走 30min Redis 缓存,
   返回 ``IPODetail.model_dump()`` 字典
2. 字典里的 ``financial_summary`` 是 ``ipos.extra.financial_summary`` 透出, 当前
   schema 是 free-form ``dict[str, Any]``; 真接入 AKShare 后会标准化成 spec/04
   §4.2 的 ``revenue_3y / net_profit_3y / gross_margin_3y / cashflow_3y / ROE``
   五元组

输出兜底
========
- ``financial_summary`` 缺失或 ``None``: 返回 ``ToolResult.success`` 但 ``data``
  携带 ``"financial_summary": None`` 与 ``"warning": "尚无财务摘要数据..."``;
  让 LLM 知道是"数据缺失" 而非"调用失败", 可以选择走 ``hybrid_search`` 招股书
  原文或显式回答用户"暂无足够数据"
- 整张 IPO 行不存在: ``ToolResult.failure``, 与 ``basic_info`` 行为一致

不在本 Tool 做
==============
- AKShare / Tushare 财务表实时拉取 (Sprint 3 BE-S3-xxx)
- 招股书内财务原文检索: 拆给 ``hybrid_search`` (BE-S2-006b)
- 同业 PE/PB 横向对比: 拆给 ``get_peer_comparison`` (BE-S2-006b)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.services import ipo_service
from app.services.agent.sandbox import sandboxed
from app.services.agent.tool_registry import Tool, ToolResult, register

_TOOL_NAME = "get_financial_statements"
_TOOL_DESCRIPTION = (
    "获取公司近 N 年财务报表关键科目（营业收入、净利润、毛利率、ROE、经营性"
    "现金流等）。当前数据源为招股书摘要 + 运营补录的结构化财务摘要；"
    "若返回 ``financial_summary=null`` 表示暂无结构化财务数据，建议改用 "
    "``hybrid_search`` 在招股书原文中检索具体科目。"
)
_TOOL_TIMEOUT = 5.0
_DEFAULT_YEARS = 3
_MAX_YEARS = 5


class GetFinancialStatementsInput(BaseModel):
    """``get_financial_statements`` 入参.

    ``years`` 与 spec/04 §3.1 对齐 (default 3). 当前阶段 ``years`` 仅作 metadata
    透传给上层 (LLM), 实际不裁剪 financial_summary 的字段; BE-S3 接 AKShare 后
    用它截 ``revenue_3y[:years]`` 等切片.
    """

    code: str = Field(
        min_length=4,
        max_length=16,
        description="新股代码（带市场后缀），如 ``0700.HK`` / ``600519.SH``。",
    )
    years: int = Field(
        default=_DEFAULT_YEARS,
        ge=1,
        le=_MAX_YEARS,
        description=f"近 N 年, 默认 {_DEFAULT_YEARS} 年, 最多 {_MAX_YEARS} 年。",
    )


@sandboxed(input_model=GetFinancialStatementsInput, timeout_seconds=_TOOL_TIMEOUT)
async def _run(args: GetFinancialStatementsInput) -> ToolResult:
    """实际查询: 委托 ``ipo_service.get_ipo_detail``, 取 ``financial_summary`` /
    ``highlights`` / ``risks`` 三块结构化字段. 缺失字段走 None / [] 兜底.
    """
    detail = await ipo_service.get_ipo_detail(args.code)
    if detail is None:
        return ToolResult.failure(
            f"未找到新股代码 {args.code}; 请确认代码是否正确（含市场后缀）"
        )

    fin = detail.get("financial_summary")
    if not isinstance(fin, dict):
        fin = None

    highlights = detail.get("highlights") or []
    if not isinstance(highlights, list):
        highlights = []

    risks = detail.get("risks") or []
    if not isinstance(risks, list):
        risks = []

    data: dict[str, Any] = {
        "code": detail.get("code"),
        "name": detail.get("name"),
        "market": detail.get("market"),
        "years_requested": args.years,
        "financial_summary": fin,
        "highlights": [str(h) for h in highlights],
        "risks": [str(r) for r in risks],
        "issue_price": detail.get("issue_price"),
        "issue_currency": detail.get("issue_currency"),
        "raised_amount": detail.get("raised_amount"),
        "pe_ratio": detail.get("pe_ratio"),
    }
    if fin is None:
        data["warning"] = (
            "暂无结构化财务摘要数据；如需具体财务科目，请改用 hybrid_search 工具"
            "在招股书原文中检索（例如 query='过去三年营业收入'）。"
        )
    return ToolResult.success(data)


register(
    Tool(
        name=_TOOL_NAME,
        description=_TOOL_DESCRIPTION,
        input_model=GetFinancialStatementsInput,
        runner=_run,
        timeout_seconds=_TOOL_TIMEOUT,
        tags=("ipo", "financial"),
    )
)


__all__ = ["GetFinancialStatementsInput"]
