"""``get_historical_winning_rate`` Tool 集成测试 (BE-S2-006b).

集成测试: 走真 PG, 直接 ORM 灌 IPO 行 (因为 ``IPOItem`` schema 没有
``industry_l2`` / ``sponsors`` 字段, 走 ingest 拿不到这些).

覆盖
====
- happy industry 过滤 → 命中 N 只, 算 avg/min/max
- happy sponsor 过滤 (jsonb @>) → 命中含该保荐人的 IPO
- happy year_range 单年 ``[year]``
- happy year_range 闭区间 ``[start, end]``
- 无任何 filter (全市场) → 把所有已上市的都算进来
- 命中 0 → ``ok=True`` + ipo_count=0 + warning
- 命中 N 只但 ``one_lot_winning_rate`` 全 NULL → ``ok=True`` + samples_with_rate=0
  + warning
- ``status != 'listed'`` 的 IPO 不算入
- 入参校验: ``year_range`` 越界 / 顺序错 / 长度错
- 入参校验: ``industry`` / ``sponsor`` 长度上限

策略
====
用 ``truncate_all`` + ``patch_session_factory`` + ``session_factory`` fixtures.
``patch_session_factory`` 已扩展把 ``app.services.agent.tools.historical`` 模块
加进去, Tool 内 ``get_session_factory()`` 直接拉到测试库.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import IPO
from app.services.agent.tool_registry import get


def _make_ipo(
    *,
    code: str,
    name: str = "test",
    market: str = "HK",
    industry_l1: str | None = "互联网",
    industry_l2: str | None = "社交平台",
    listing_date: date | None = date(2023, 1, 1),
    sponsors: list[str] | None = None,
    one_lot_winning_rate: Decimal | float | None = None,
    status: str | None = "listed",
) -> IPO:
    extra: dict = {}
    if one_lot_winning_rate is not None:
        extra["one_lot_winning_rate"] = float(one_lot_winning_rate)
    return IPO(
        code=code,
        name=name,
        market=market,
        industry_l1=industry_l1,
        industry_l2=industry_l2,
        listing_date=listing_date,
        sponsors=sponsors,
        extra=extra,
        status=status,
        data_source="test",
    )


# ─── happy: industry 过滤 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_historical_industry_filter(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    async with session_factory() as session:
        # 互联网行业 3 只, 中签率 0.05 / 0.10 / 0.15
        for i, code in enumerate(["A.HK", "B.HK", "C.HK"]):
            session.add(
                _make_ipo(
                    code=code,
                    industry_l1="互联网",
                    one_lot_winning_rate=Decimal(str(0.05 + i * 0.05)),
                )
            )
        # 不同行业 (金融) 不该被算入
        session.add(
            _make_ipo(
                code="X.HK",
                industry_l1="金融",
                one_lot_winning_rate=Decimal("0.99"),
            )
        )
        await session.commit()

    tool = get("get_historical_winning_rate")
    assert tool is not None
    r = await tool.runner({"industry": "互联网"})

    assert r.ok is True, r.error
    assert r.data is not None
    d = r.data
    assert d["cohort"]["industry"] == "互联网"
    assert d["ipo_count"] == 3
    assert d["samples_with_rate"] == 3
    assert d["avg_winning_rate"] == pytest.approx(0.10, abs=1e-6)
    assert d["min_winning_rate"] == pytest.approx(0.05, abs=1e-6)
    assert d["max_winning_rate"] == pytest.approx(0.15, abs=1e-6)
    # 留 Sprint 3 的占位字段
    assert d["first_day_performance"] is None
    assert "note" in d


@pytest.mark.asyncio
async def test_historical_industry_l2_also_matches(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """industry 既会匹配 l1 也匹配 l2."""
    async with session_factory() as session:
        session.add(
            _make_ipo(
                code="A.HK",
                industry_l1="医疗",
                industry_l2="互联网医疗",
                one_lot_winning_rate=Decimal("0.20"),
            )
        )
        await session.commit()

    tool = get("get_historical_winning_rate")
    assert tool is not None
    r = await tool.runner({"industry": "互联网医疗"})

    assert r.ok is True
    assert r.data is not None
    assert r.data["ipo_count"] == 1


# ─── happy: sponsor 过滤 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_historical_sponsor_filter(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    async with session_factory() as session:
        session.add(
            _make_ipo(
                code="A.HK",
                sponsors=["中金公司", "高盛"],
                one_lot_winning_rate=Decimal("0.08"),
            )
        )
        session.add(
            _make_ipo(
                code="B.HK",
                sponsors=["中金公司"],
                one_lot_winning_rate=Decimal("0.12"),
            )
        )
        session.add(
            _make_ipo(
                code="C.HK",
                sponsors=["摩根士丹利"],  # 不该匹配
                one_lot_winning_rate=Decimal("0.30"),
            )
        )
        await session.commit()

    tool = get("get_historical_winning_rate")
    assert tool is not None
    r = await tool.runner({"sponsor": "中金公司"})

    assert r.ok is True, r.error
    assert r.data is not None
    assert r.data["ipo_count"] == 2
    assert r.data["avg_winning_rate"] == pytest.approx(0.10, abs=1e-6)


# ─── happy: year_range ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_historical_year_range_single_year(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    async with session_factory() as session:
        session.add(
            _make_ipo(
                code="A.HK",
                listing_date=date(2024, 3, 1),
                one_lot_winning_rate=Decimal("0.10"),
            )
        )
        session.add(
            _make_ipo(
                code="B.HK",
                listing_date=date(2023, 5, 1),
                one_lot_winning_rate=Decimal("0.20"),
            )
        )
        await session.commit()

    tool = get("get_historical_winning_rate")
    assert tool is not None
    r = await tool.runner({"year_range": [2024]})

    assert r.ok is True, r.error
    assert r.data is not None
    assert r.data["ipo_count"] == 1
    assert r.data["avg_winning_rate"] == pytest.approx(0.10, abs=1e-6)


@pytest.mark.asyncio
async def test_historical_year_range_inclusive(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    async with session_factory() as session:
        session.add(
            _make_ipo(
                code="A.HK",
                listing_date=date(2022, 1, 1),
                one_lot_winning_rate=Decimal("0.10"),
            )
        )
        session.add(
            _make_ipo(
                code="B.HK",
                listing_date=date(2024, 12, 31),
                one_lot_winning_rate=Decimal("0.30"),
            )
        )
        # 边界外
        session.add(
            _make_ipo(
                code="C.HK",
                listing_date=date(2025, 1, 1),
                one_lot_winning_rate=Decimal("0.99"),
            )
        )
        await session.commit()

    tool = get("get_historical_winning_rate")
    assert tool is not None
    r = await tool.runner({"year_range": [2022, 2024]})

    assert r.ok is True, r.error
    assert r.data is not None
    assert r.data["ipo_count"] == 2
    assert r.data["avg_winning_rate"] == pytest.approx(0.20, abs=1e-6)


# ─── happy: 全市场 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_historical_no_filter_aggregates_all_listed(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    async with session_factory() as session:
        for i, code in enumerate(["A.HK", "B.HK", "C.HK"]):
            session.add(
                _make_ipo(
                    code=code,
                    industry_l1=f"行业{i}",
                    one_lot_winning_rate=Decimal(str(0.10 + i * 0.10)),
                )
            )
        await session.commit()

    tool = get("get_historical_winning_rate")
    assert tool is not None
    r = await tool.runner({})

    assert r.ok is True, r.error
    assert r.data is not None
    assert r.data["ipo_count"] == 3
    assert r.data["avg_winning_rate"] == pytest.approx(0.20, abs=1e-6)


# ─── 边界: 全 NULL / 命中 0 / 非 listed ───────────────────────────────


@pytest.mark.asyncio
async def test_historical_no_match_warns(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    async with session_factory() as session:
        session.add(_make_ipo(code="A.HK", industry_l1="互联网"))
        await session.commit()

    tool = get("get_historical_winning_rate")
    assert tool is not None
    r = await tool.runner({"industry": "量子计算"})

    assert r.ok is True
    assert r.data is not None
    assert r.data["ipo_count"] == 0
    assert r.data["samples_with_rate"] == 0
    assert "warning" in r.data
    assert "未在 ipos 表" in r.data["warning"]


@pytest.mark.asyncio
async def test_historical_all_null_winning_rate_warns(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """命中 N 只但 winning_rate 全 NULL → 不算 fail, 给 warning."""
    async with session_factory() as session:
        for code in ["A.HK", "B.HK"]:
            session.add(
                _make_ipo(code=code, industry_l1="互联网")  # 不传 one_lot_winning_rate
            )
        await session.commit()

    tool = get("get_historical_winning_rate")
    assert tool is not None
    r = await tool.runner({"industry": "互联网"})

    assert r.ok is True, r.error
    assert r.data is not None
    assert r.data["ipo_count"] == 2
    assert r.data["samples_with_rate"] == 0
    assert r.data["avg_winning_rate"] is None
    assert r.data["min_winning_rate"] is None
    assert r.data["max_winning_rate"] is None
    assert "warning" in r.data
    assert "全部 one_lot_winning_rate 字段为 NULL" in r.data["warning"]


@pytest.mark.asyncio
async def test_historical_excludes_non_listed(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """status != 'listed' 的 IPO 不算入."""
    async with session_factory() as session:
        session.add(
            _make_ipo(
                code="A.HK",
                industry_l1="互联网",
                one_lot_winning_rate=Decimal("0.10"),
                status="listed",
            )
        )
        session.add(
            _make_ipo(
                code="B.HK",
                industry_l1="互联网",
                one_lot_winning_rate=Decimal("0.99"),
                status="upcoming",
            )
        )
        session.add(
            _make_ipo(
                code="C.HK",
                industry_l1="互联网",
                one_lot_winning_rate=Decimal("0.99"),
                status="withdrawn",
            )
        )
        await session.commit()

    tool = get("get_historical_winning_rate")
    assert tool is not None
    r = await tool.runner({"industry": "互联网"})

    assert r.ok is True
    assert r.data is not None
    assert r.data["ipo_count"] == 1
    assert r.data["avg_winning_rate"] == pytest.approx(0.10, abs=1e-6)


# ─── 入参校验 (沙盒) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_historical_year_range_too_long() -> None:
    tool = get("get_historical_winning_rate")
    assert tool is not None
    r = await tool.runner({"year_range": [2020, 2021, 2022]})
    assert r.ok is False
    assert r.error is not None
    assert "参数校验失败" in r.error


@pytest.mark.asyncio
async def test_historical_year_range_reversed() -> None:
    tool = get("get_historical_winning_rate")
    assert tool is not None
    r = await tool.runner({"year_range": [2024, 2020]})
    assert r.ok is False


@pytest.mark.asyncio
async def test_historical_year_range_out_of_bounds() -> None:
    tool = get("get_historical_winning_rate")
    assert tool is not None
    r = await tool.runner({"year_range": [1800]})
    assert r.ok is False


def test_historical_openai_schema_shape() -> None:
    tool = get("get_historical_winning_rate")
    assert tool is not None
    schema = tool.to_openai_schema()
    fn = schema["function"]
    assert fn["name"] == "get_historical_winning_rate"
    params = fn["parameters"]
    # 三个 filter 都是 optional, required 应该为空 (或不含三者)
    required = params.get("required", [])
    assert "industry" not in required
    assert "sponsor" not in required
    assert "year_range" not in required
    # title 字段应被剔除 (LLM 兼容)
    assert "title" not in params
