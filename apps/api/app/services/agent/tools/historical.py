"""``get_historical_winning_rate`` Tool — 历史中签率聚合 (BE-S2-006b).

对应 spec/04 §3.1 第 5 个 Tool. 给定行业 / 保荐人 / 年份范围, 聚合统计同 cohort
历史新股的中签率与上市新股数量.

数据源
======
``ipos`` 表 (BE-007/BE-S2-000 已落):
- ``industry_l1`` / ``industry_l2``: 行业过滤
- ``sponsors``: ``JSONB`` 列, 是 ``list[str]`` (保荐人名单, BE-S2-000 hkex
  ingest 解析); 用 PG ``@>`` jsonb contains 操作符匹配
- ``listing_date``: ``Date``, 用于年份范围过滤
- ``status = 'listed'``: 只统计已上市 (上面才能算中签率)

⚠️ ``one_lot_winning_rate`` 不是 IPO ORM 直接列, 而是存在 ``extra.one_lot_winning_rate``
JSONB 内 (BE-007 ipo_ingest_service 写入约定). 这里走 PG ``->>`` 操作符 + cast
``numeric`` 提取.

聚合维度
========
返回字段:
- ``ipo_count``: 命中 cohort 的已上市新股数量
- ``avg_winning_rate``: 中签率均值 (排除 NULL)
- ``min_winning_rate`` / ``max_winning_rate``: 极值
- ``samples_with_rate``: 实际有 winning_rate 数据的样本数 (NULL 不计)
- ``cohort``: 入参回显, 让 LLM 知道筛选条件

为什么走 raw SQL 而非 ORM
==========================
SQLAlchemy ORM 不直接支持 JSONB ``->>`` + cast numeric 的语法链, 写出来比 raw
SQL 还啰嗦; 反正本 Tool 唯一一条 SQL, 走 ``text(...)`` 配 ``bindparam`` 更清楚.
spec/06 BE-S2-005 hybrid_search 也是 raw SQL 路线, 这里保持风格一致.

为什么不同时返回"首日表现统计" (spec 原文有提)
================================================
**首日表现 = 上市首日 K 线 (开盘价 vs 发行价 涨跌幅)**, 这块需要:
- 首日 K 线源 (AKShare ``stock_zh_a_hist`` / Futu OpenAPI)
- 历史 IPO 表里没有 ``first_day_close`` 字段, BE-007 schema 也没设计这一列

→ 当前 PR 只兜中签率, "首日表现"留口给 Sprint 3; ``data`` 里返回
``first_day_performance: null`` 显式占位, 配 note, 让 LLM 知道字段存在但当前
不可用.

入参兼容
========
- ``industry`` / ``sponsor`` / ``year_range`` 都是 optional, 都没传时算"全市场
  历史"统计 (允许, LLM 可能问"过去 5 年港股 IPO 平均中签率")
- ``year_range`` 走 ``[start, end]`` 闭区间; 单元素 ``[year]`` 视为单年; 长度
  > 2 的 list 拒收 (pydantic 走 list-length 校验)

不在本 Tool 做
==============
- 走 RAG 检索"招股书 + 历史报告 + 媒体文章" (BE-S2-006b 同 PR 的 hybrid_search Tool)
- 上下游券商对比 (Sprint 3 券商模块)
- 暗盘 / 灰盘价 (Sprint 3 实时行情接入)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import bindparam, text
from sqlalchemy.types import Integer, String

from app.db.base import get_session_factory
from app.services.agent.sandbox import sandboxed
from app.services.agent.tool_registry import Tool, ToolResult, register

_TOOL_NAME = "get_historical_winning_rate"
_TOOL_DESCRIPTION = (
    "查询同行业 / 同保荐人 / 同年份范围的历史新股中签率统计（均值 / 极值 / 样本数）。"
    "返回 ipo_count（命中 cohort 的已上市新股数）、avg_winning_rate、min/max、"
    "samples_with_rate（实际有 winning_rate 数据的样本数）。"
    "**首日表现统计字段当前为 null**（K 线源未接入，留口 Sprint 3 接 AKShare/Futu）。"
)
_TOOL_TIMEOUT = 5.0


class GetHistoricalWinningRateInput(BaseModel):
    """``get_historical_winning_rate`` 入参. 三个过滤条件都是 optional."""

    industry: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "行业名 (按 industry_l1 或 industry_l2 任一匹配); 不传则不按行业过滤。"
        ),
    )
    sponsor: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "保荐人名 (sponsors JSONB 数组成员匹配); 不传则不按保荐人过滤。"
        ),
    )
    year_range: list[int] | None = Field(
        default=None,
        description=(
            "年份范围 ``[start, end]`` 闭区间 (按 listing_date 年份匹配); "
            "单元素 ``[year]`` 视为单年; 不传则不限年份。"
        ),
    )

    @field_validator("year_range")
    @classmethod
    def _validate_year_range(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return None
        if not isinstance(v, list) or not 1 <= len(v) <= 2:
            raise ValueError("year_range 必须是 1-2 个整数的 list (如 [2020] 或 [2020, 2024])")
        for y in v:
            if not isinstance(y, int) or not 1990 <= y <= 2100:
                raise ValueError(f"year {y} 不在合理范围 (1990-2100)")
        if len(v) == 2 and v[0] > v[1]:
            raise ValueError(f"year_range start={v[0]} 大于 end={v[1]}")
        return v


# 拼装 raw SQL: 走 PG ``extra->>'one_lot_winning_rate'`` 提取后 ``::numeric`` cast,
# 一条 SQL 拿 5 个聚合值
_BASE_SQL = """
SELECT
    COUNT(*) AS ipo_count,
    COUNT(NULLIF((extra->>'one_lot_winning_rate'), '')) AS samples_with_rate,
    AVG(NULLIF((extra->>'one_lot_winning_rate'), '')::numeric) AS avg_rate,
    MIN(NULLIF((extra->>'one_lot_winning_rate'), '')::numeric) AS min_rate,
    MAX(NULLIF((extra->>'one_lot_winning_rate'), '')::numeric) AS max_rate
FROM ipos
WHERE status = 'listed'
  {industry_clause}
  {sponsor_clause}
  {year_clause}
"""


def _build_sql_and_params(
    args: GetHistoricalWinningRateInput,
) -> tuple[str, dict[str, Any]]:
    """组装 SQL 子句 + bindparams."""
    params: dict[str, Any] = {}

    industry_clause = ""
    if args.industry:
        industry_clause = "AND (industry_l1 = :industry OR industry_l2 = :industry)"
        params["industry"] = args.industry.strip()

    sponsor_clause = ""
    if args.sponsor:
        sponsor_clause = "AND sponsors @> CAST(:sponsor_jsonb AS jsonb)"
        # 用 json.dumps 安全转义 (引号 / 反斜杠都能处理)
        import json as _json

        params["sponsor_jsonb"] = _json.dumps([args.sponsor.strip()], ensure_ascii=False)

    year_clause = ""
    if args.year_range:
        if len(args.year_range) == 1:
            year_clause = "AND EXTRACT(YEAR FROM listing_date) = :year_start"
            params["year_start"] = args.year_range[0]
        else:
            year_clause = (
                "AND EXTRACT(YEAR FROM listing_date) >= :year_start "
                "AND EXTRACT(YEAR FROM listing_date) <= :year_end"
            )
            params["year_start"] = args.year_range[0]
            params["year_end"] = args.year_range[1]

    sql = _BASE_SQL.format(
        industry_clause=industry_clause,
        sponsor_clause=sponsor_clause,
        year_clause=year_clause,
    )
    return sql, params


@sandboxed(input_model=GetHistoricalWinningRateInput, timeout_seconds=_TOOL_TIMEOUT)
async def _run(args: GetHistoricalWinningRateInput) -> ToolResult:
    """聚合查询 ipos 表, 走 raw SQL 一次查 5 个聚合值."""
    sql, params = _build_sql_and_params(args)

    binds: list[Any] = []
    if "industry" in params:
        binds.append(bindparam("industry", type_=String()))
    if "sponsor_jsonb" in params:
        binds.append(bindparam("sponsor_jsonb", type_=String()))
    if "year_start" in params:
        binds.append(bindparam("year_start", type_=Integer()))
    if "year_end" in params:
        binds.append(bindparam("year_end", type_=Integer()))

    stmt = text(sql)
    if binds:
        stmt = stmt.bindparams(*binds)

    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(stmt, params)).mappings().one()

    def _to_float(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    ipo_count = int(row.get("ipo_count") or 0)
    samples_with_rate = int(row.get("samples_with_rate") or 0)

    data: dict[str, Any] = {
        "cohort": {
            "industry": args.industry,
            "sponsor": args.sponsor,
            "year_range": args.year_range,
        },
        "ipo_count": ipo_count,
        "samples_with_rate": samples_with_rate,
        "avg_winning_rate": _to_float(row.get("avg_rate")),
        "min_winning_rate": _to_float(row.get("min_rate")),
        "max_winning_rate": _to_float(row.get("max_rate")),
        "first_day_performance": None,  # 留口 Sprint 3
    }

    if ipo_count == 0:
        data["warning"] = (
            f"未在 ipos 表找到符合 cohort=(industry={args.industry!r}, "
            f"sponsor={args.sponsor!r}, year_range={args.year_range!r}) 的已上市新股；"
            "可能筛选条件过严或样本不足。"
        )
    elif samples_with_rate == 0:
        data["warning"] = (
            f"命中 {ipo_count} 只已上市新股, 但全部 one_lot_winning_rate 字段为 NULL "
            f"(数据源未回填中签率)；avg/min/max 均为 null。"
        )
    else:
        data["note"] = (
            "首日表现统计 (first_day_performance) 暂未接入 K 线数据源, 留待 Sprint 3 补齐。"
        )
    return ToolResult.success(data)


register(
    Tool(
        name=_TOOL_NAME,
        description=_TOOL_DESCRIPTION,
        input_model=GetHistoricalWinningRateInput,
        runner=_run,
        timeout_seconds=_TOOL_TIMEOUT,
        tags=("ipo", "historical"),
    )
)


__all__ = ["GetHistoricalWinningRateInput"]
