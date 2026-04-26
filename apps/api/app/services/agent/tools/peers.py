"""``get_peer_comparison`` Tool — 同业横向对标 (BE-S2-006b).

对应 spec/04 §3.1 第 3 个 Tool. 给定 IPO code, 找同行业 3-5 家可比公司, 横向
对比 PE / 募资额 / 财务摘要里的关键科目.

数据源
======
``ipos`` 表 (BE-007/BE-S2-000 已落):
- ``industry_l1`` / ``industry_l2``: 行业一级/二级分类, 走 industry_l2 优先, 退
  industry_l1 兜底
- ``pe_ratio``: 直接列, 一定可用
- ``raised_amount``: 直接列, 用于 size 维度
- ``extra.financial_summary``: BE-S2-004 招股书 RAG 写入 + 运营手动补;
  ``ROE / GrossMargin / Revenue / PB`` 这些科目从这里取, 缺则 None

为什么不接 AKShare 行业对比接口
================================
1. AKShare ``stock_industry_pe_lg`` 只覆盖 A 股, 港股没有
2. 即使 A 股能拉, 行业指数级 PE 与"同期一起 IPO 的同行可比公司"不是一个概念;
   spec/04 §3.1 原意是"找最近上市的可比新股", IPO 表本身就是真相
3. 留口给 Sprint 3: BE-S3 接 AKShare 后再加"行业指数 PE 中位数"作为 baseline
   字段, 但**当前 Tool 接口不变**, 实现层切就行

排序与选择
==========
1. 必须排除自己 (``code != target.code``)
2. 优先同 ``industry_l2`` (更精细); 数量不够再 fallback 到 ``industry_l1``
3. 在候选池里按"上市日期 DESC NULLS LAST"取 limit 个 (最新可比新股最有参考意义)
4. 没有任何同行业可比 (新行业第一只 IPO) → 返回空 ``peers=[]`` + warning,
   ``ok=True`` (不算调用失败, 让 LLM 知道是"无可比" 而非"调用错")

dimensions 字段约束
====================
spec 写的 enum 是 ``['PE', 'PB', 'ROE', 'GrossMargin', 'Revenue']``. 当前实现:
- ``PE`` → 直接 ``ipos.pe_ratio``
- ``PB`` / ``ROE`` / ``GrossMargin`` / ``Revenue`` → 从 ``extra.financial_summary``
  按 key 提取 (大小写不敏感: financial_summary 写入侧约定 lower_snake, 这里两个都试)
- 未在 enum 里的 dim 名 → pydantic 会拒收 (Literal 校验)

不在本 Tool 做
==============
- 行业聚合统计（行业 PE 中位数 / 平均 ROE）: 那是 ``get_historical_winning_rate``
  的范畴 (按"同行业历史 IPO"聚合)
- 财务三大表科目级对比: 招股书原文级别, 走 ``hybrid_search``
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.db.base import get_session_factory
from app.db.models.ipo import IPO
from app.services.agent.sandbox import sandboxed
from app.services.agent.tool_registry import Tool, ToolResult, register

_TOOL_NAME = "get_peer_comparison"
_TOOL_DESCRIPTION = (
    "基于行业找出 3-5 家可比公司并做横向对比。返回的可比公司一定来自 ipos 表"
    "（即近期已招股 / 上市的同行业新股），不是行业指数；优先匹配 industry_l2，"
    "不够再 fallback industry_l1；按上市日期降序取最新 N 家。"
    "若 financial_summary 中缺该 dimension，对应字段为 null（请改用 hybrid_search "
    "在招股书原文中检索具体科目）。"
)
_TOOL_TIMEOUT = 5.0
_DEFAULT_LIMIT = 5
_MAX_LIMIT = 10


# Literal 限制 dimensions 取值, 与 spec/04 §3.1 严格对齐 (新增维度先改 spec)
PeerDimension = Literal["PE", "PB", "ROE", "GrossMargin", "Revenue"]
_ALL_DIMENSIONS: tuple[PeerDimension, ...] = ("PE", "PB", "ROE", "GrossMargin", "Revenue")


class GetPeerComparisonInput(BaseModel):
    """``get_peer_comparison`` 入参. ``dimensions`` 不传时默认全 5 维."""

    code: str = Field(
        min_length=4,
        max_length=16,
        description="目标新股代码（带市场后缀），如 ``0700.HK`` / ``600519.SH``。",
    )
    dimensions: list[PeerDimension] | None = Field(
        default=None,
        description=(
            "横向对比维度，可选 ``PE`` / ``PB`` / ``ROE`` / ``GrossMargin`` / "
            "``Revenue``。不传则返回全部 5 维。"
        ),
    )
    limit: int = Field(
        default=_DEFAULT_LIMIT,
        ge=1,
        le=_MAX_LIMIT,
        description=f"返回的可比公司数量上限, 默认 {_DEFAULT_LIMIT}, 最多 {_MAX_LIMIT}。",
    )


def _extract_dimension(
    pe_ratio: Any,
    financial_summary: dict[str, Any] | None,
    dim: PeerDimension,
) -> float | None:
    """从 ``ipos.pe_ratio`` 或 ``extra.financial_summary`` 中提取一个 dimension.

    financial_summary 的 key 命名约定为 lower_snake (``pb`` / ``roe`` /
    ``gross_margin`` / ``revenue``), 但运营手填时大小写不一定规范, 这里小写归一
    后再查表.
    """
    if dim == "PE":
        return float(pe_ratio) if pe_ratio is not None else None

    if not isinstance(financial_summary, dict):
        return None

    # snake_case 归一查表
    key_map: dict[PeerDimension, tuple[str, ...]] = {
        "PB": ("pb", "p_b"),
        "ROE": ("roe",),
        "GrossMargin": ("gross_margin", "grossmargin"),
        "Revenue": ("revenue", "revenue_latest", "revenue_ttm"),
    }
    candidates = key_map.get(dim, ())
    fin_lower = {str(k).lower(): v for k, v in financial_summary.items()}
    for key in candidates:
        if key in fin_lower:
            v = fin_lower[key]
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
    return None


def _row_to_peer(
    row: IPO,
    *,
    dimensions: list[PeerDimension],
) -> dict[str, Any]:
    """``ipos`` 行 → peer 字典 (LLM 可读).

    metrics 字段是 ``{dimension: value or null}``; 不要把 None 字段直接丢掉,
    保留 null 是给 LLM 做"是 'PE 缺失' 还是 '没有该 dim'"的二分判断.
    """
    extra = row.extra if isinstance(row.extra, dict) else {}
    fin = extra.get("financial_summary") if isinstance(extra, dict) else None
    if not isinstance(fin, dict):
        fin = None

    metrics = {dim: _extract_dimension(row.pe_ratio, fin, dim) for dim in dimensions}
    return {
        "code": row.code,
        "name": row.name,
        "market": row.market,
        "industry_l1": row.industry_l1,
        "industry_l2": row.industry_l2,
        "listing_date": row.listing_date.isoformat() if row.listing_date else None,
        "raised_amount": float(row.raised_amount) if row.raised_amount is not None else None,
        "issue_currency": row.issue_currency,
        "metrics": metrics,
    }


@sandboxed(input_model=GetPeerComparisonInput, timeout_seconds=_TOOL_TIMEOUT)
async def _run(args: GetPeerComparisonInput) -> ToolResult:
    """实际查询: 走 ipos 表, 按 industry_l2 → industry_l1 fallback 找 N 个 peer."""
    code_upper = args.code.upper().strip()
    dimensions = list(args.dimensions) if args.dimensions else list(_ALL_DIMENSIONS)

    factory = get_session_factory()
    async with factory() as session:
        # 1. 查目标 IPO 的 industry
        target = (
            await session.execute(select(IPO).where(IPO.code == code_upper))
        ).scalar_one_or_none()
        if target is None:
            return ToolResult.failure(
                f"未找到新股代码 {args.code}; 请确认代码是否正确（含市场后缀）"
            )

        # 2. 同 industry_l2 优先, 数量不够 fallback industry_l1
        peer_rows: list[IPO] = []

        if target.industry_l2:
            peer_rows = list(
                (
                    await session.execute(
                        select(IPO)
                        .where(
                            IPO.code != code_upper,
                            IPO.industry_l2 == target.industry_l2,
                        )
                        .order_by(IPO.listing_date.desc().nullslast())
                        .limit(args.limit)
                    )
                ).scalars()
            )

        if len(peer_rows) < args.limit and target.industry_l1:
            existing_codes = {r.code for r in peer_rows} | {code_upper}
            need = args.limit - len(peer_rows)
            # existing_codes 至少含 code_upper, 不为空; 直接 notin_ 不需要短路
            extra_rows = list(
                (
                    await session.execute(
                        select(IPO)
                        .where(
                            IPO.code.notin_(existing_codes),
                            or_(
                                IPO.industry_l1 == target.industry_l1,
                                IPO.industry_l2 == target.industry_l1,  # 兼容主辅分类倒置
                            ),
                        )
                        .order_by(IPO.listing_date.desc().nullslast())
                        .limit(need)
                    )
                ).scalars()
            )
            peer_rows.extend(extra_rows)

    peers = [_row_to_peer(r, dimensions=dimensions) for r in peer_rows]

    data: dict[str, Any] = {
        "target": {
            "code": target.code,
            "name": target.name,
            "market": target.market,
            "industry_l1": target.industry_l1,
            "industry_l2": target.industry_l2,
        },
        "dimensions": dimensions,
        "peers": peers,
        "peer_count": len(peers),
    }
    if not peers:
        data["warning"] = (
            f"未在 ipos 表找到 industry_l1={target.industry_l1!r} / "
            f"industry_l2={target.industry_l2!r} 同行业的可比新股；可能是该行业第一只 "
            f"IPO 或样本不足。建议改用 hybrid_search 在招股书中找'同业'/'竞争对手'章节。"
        )
    return ToolResult.success(data)


register(
    Tool(
        name=_TOOL_NAME,
        description=_TOOL_DESCRIPTION,
        input_model=GetPeerComparisonInput,
        runner=_run,
        timeout_seconds=_TOOL_TIMEOUT,
        tags=("ipo", "peers"),
    )
)


__all__ = ["GetPeerComparisonInput", "PeerDimension"]
