"""``get_ipo_basic_info`` Tool — 新股发行基础信息 (BE-S2-006a).

对应 spec/04 §3.1 第 1 个 Tool. 给 LLM 的 description / parameters 都按 spec
原文对齐, 让 BE-S2-007 LangGraph ReAct 主循环能直接 ``tools=list_openai_schemas()``
注入 LLM context.

数据来源
========
直接复用 ``app.services.ipo_service.get_ipo`` (Sprint 1 已落 + BE-S2-000 升级到
DB 路径). 它返回 ``IPOItem`` (轻量字段) 已经够用; ``prospectus_url`` /
``sponsors`` / ``underwriters`` 这种"详情字段"不属于 basic_info 范围 (这块属于
``get_financial_statements`` 与 BE-S2-006b 的 hybrid_search), 不重复返回.

输出字段
========
全部 ``Decimal`` / ``date`` / ``datetime`` 都序列化为 JSON 友好类型 (string /
ISO format / float). 让 BE-S2-007 主循环把 ``ToolResult.data`` 直接
``json.dumps(...)`` 当作 OpenAI ``tool`` 角色 message content.

不在本 Tool 做
==============
- 实时盘口 / 暗盘价: 当前 IPO 表不存; 走 BE-S2-006b ``hybrid_search`` 或后续
  Futu/AKShare 实时接入
- 同行业对比 / 历史中签率: 拆给 ``get_peer_comparison`` /
  ``get_historical_winning_rate`` (BE-S2-006b)
- 招股书原文检索: 拆给 ``hybrid_search`` (BE-S2-006b)
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.services import ipo_service
from app.services.agent.sandbox import sandboxed
from app.services.agent.tool_registry import Tool, ToolResult, register

_TOOL_NAME = "get_ipo_basic_info"
_TOOL_DESCRIPTION = (
    "获取新股的发行基础信息（含发行价、市盈率、募资额、招股区间、上市日期、"
    "行业分类、发行状态等）。仅返回结构化字段，不返回招股书原文摘要 / 同行业对比 / "
    "情感分析（这些请改用其它工具）。"
)
_TOOL_TIMEOUT = 5.0


class GetIpoBasicInfoInput(BaseModel):
    """``get_ipo_basic_info`` 入参 schema.

    ``code`` 走 IPOItem.code 同款带市场后缀格式 (HK: ``0700.HK``, A: ``600519.SH``);
    主循环 LLM 给的 code 我们小写归一 / 大写归一统一在 ipo_service 内部.
    """

    code: str = Field(
        min_length=4,
        max_length=16,
        description=(
            "新股代码（带市场后缀），如港股 ``0700.HK`` / A 股 ``600519.SH``。"
            "未带后缀时默认按 A 股处理。"
        ),
    )


def _coerce_decimal(v: Decimal | None) -> float | None:
    """``Decimal`` → ``float`` 给 LLM 看 (LLM 不识别 Decimal). 精度有 1e-6 级损失,
    但 JSON 数字传 LLM 时本来就是 IEEE 754, 这里提前 cast 反而避免 OpenAI client
    再次序列化时的 ``decimal.Decimal is not JSON serializable`` 报错.
    """
    return float(v) if v is not None else None


def _coerce_date(v: date | datetime | None) -> str | None:
    """ISO 8601 字符串. ``datetime`` 走 ``isoformat()`` 含时区; ``date`` 走 ``YYYY-MM-DD``."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return v.isoformat()


@sandboxed(input_model=GetIpoBasicInfoInput, timeout_seconds=_TOOL_TIMEOUT)
async def _run(args: GetIpoBasicInfoInput) -> ToolResult:
    """实际查询: 委托 ``ipo_service.get_ipo``, 失败 / 不存在归一为 ToolResult.failure."""
    item = await ipo_service.get_ipo(args.code)
    if item is None:
        return ToolResult.failure(f"未找到新股代码 {args.code}; 请确认代码是否正确（含市场后缀）")

    data: dict[str, Any] = {
        "code": item.code,
        "name": item.name,
        "market": item.market,
        "industry": item.industry,
        "issue_price": _coerce_decimal(item.issue_price),
        "issue_currency": item.issue_currency,
        "listing_date": _coerce_date(item.listing_date),
        "subscribe_start": _coerce_date(item.subscribe_start),
        "subscribe_end": _coerce_date(item.subscribe_end),
        "pe_ratio": _coerce_decimal(item.pe_ratio),
        "raised_amount": _coerce_decimal(item.raised_amount),
        "one_lot_winning_rate": _coerce_decimal(item.one_lot_winning_rate),
        "status": item.status,
        "data_source": item.data_source,
        "updated_at": _coerce_date(item.updated_at),
    }
    return ToolResult.success(data)


# 模块级 side effect: import 触发注册
register(
    Tool(
        name=_TOOL_NAME,
        description=_TOOL_DESCRIPTION,
        input_model=GetIpoBasicInfoInput,
        runner=_run,
        timeout_seconds=_TOOL_TIMEOUT,
        tags=("ipo", "basic"),
    )
)


__all__ = ["GetIpoBasicInfoInput"]
