"""``get_peer_comparison`` Tool 集成测试 (BE-S2-006b).

集成测试: 走真 PG, 直接 ORM 灌 IPO 行 (因为 ``IPOItem`` schema 没有
``industry_l2`` / ``sponsors`` 字段, 走 ``ipo_ingest_service.upsert_ipos``
拿不到 industry_l2).

覆盖
====
- happy industry_l2 优先: 同 l2 的同行业取 N 个, 排除自己
- fallback industry_l1: l2 不够时回退 l1
- 没有同行业 → ``ok=True`` + 空 peers + warning
- target IPO 不存在 → ``ok=False`` + failure
- dimensions 默认全 5 维 / 自定义 dims 子集
- limit 上限
- ``extra.financial_summary`` 缺失 / 部分缺 → metrics 字段 None
- 入参校验: code 长度

策略
====
用 ``truncate_all`` + ``patch_session_factory`` + ``session_factory`` fixtures.
``patch_session_factory`` 已扩展把 ``app.services.agent.tools.peers`` 模块加进去
(见 ``conftest.py``), 所以 Tool 内 ``get_session_factory()`` 直接拉到测试库.
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
    name: str,
    market: str = "HK",
    industry_l1: str | None = "互联网",
    industry_l2: str | None = "社交平台",
    listing_date: date | None = date(2024, 1, 1),
    pe_ratio: Decimal | None = Decimal("20.5"),
    raised_amount: Decimal | None = Decimal("1000000000"),
    issue_currency: str | None = "HKD",
    sponsors: list[str] | None = None,
    extra: dict | None = None,
    status: str | None = "listed",
) -> IPO:
    return IPO(
        code=code,
        name=name,
        market=market,
        industry_l1=industry_l1,
        industry_l2=industry_l2,
        listing_date=listing_date,
        pe_ratio=pe_ratio,
        raised_amount=raised_amount,
        issue_currency=issue_currency,
        sponsors=sponsors,
        extra=extra or {},
        status=status,
        data_source="test",
    )


@pytest.mark.asyncio
async def test_peers_industry_l2_priority(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """同 industry_l2 优先, 取最新 N 个 (按 listing_date DESC), 排除自己."""
    async with session_factory() as session:
        # target
        session.add(
            _make_ipo(
                code="0700.HK",
                name="腾讯控股",
                industry_l1="互联网",
                industry_l2="社交平台",
                listing_date=date(2004, 6, 16),
                extra={
                    "financial_summary": {
                        "roe": 0.25,
                        "gross_margin": 0.45,
                        "revenue": 5500.0,
                    }
                },
            )
        )
        # 同 l2 (社交平台) × 3, listing_date 不同, 应按时间排序拿前 limit 个
        for i, code in enumerate(["3690.HK", "9988.HK", "9999.HK"]):
            session.add(
                _make_ipo(
                    code=code,
                    name=f"PEER{i}",
                    industry_l1="互联网",
                    industry_l2="社交平台",
                    listing_date=date(2024, i + 1, 1),  # 越大越新
                    pe_ratio=Decimal(f"{30 + i}.0"),
                    extra={
                        "financial_summary": {
                            "roe": 0.10 + i * 0.01,
                            "gross_margin": 0.30,
                            "revenue": 1000.0 + i,
                        }
                    },
                )
            )
        # 同 l1 但不同 l2 (不该被优先选)
        session.add(
            _make_ipo(
                code="DIFFL2.HK",
                name="L1Only",
                industry_l1="互联网",
                industry_l2="电商",
            )
        )
        await session.commit()

    tool = get("get_peer_comparison")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK", "limit": 2})

    assert r.ok is True, r.error
    assert r.data is not None
    d = r.data
    assert d["target"]["code"] == "0700.HK"
    assert d["target"]["industry_l2"] == "社交平台"
    assert d["peer_count"] == 2
    peer_codes = [p["code"] for p in d["peers"]]
    # 同 l2 的 3 只里, 按 listing_date DESC 取前 2: 9999 / 9988
    assert peer_codes == ["9999.HK", "9988.HK"]
    # 自己不在 peers 里
    assert "0700.HK" not in peer_codes
    # 不同 l2 的不被优先
    assert "DIFFL2.HK" not in peer_codes
    # dimensions 默认全 5 维
    assert d["dimensions"] == ["PE", "PB", "ROE", "GrossMargin", "Revenue"]
    # metrics 形状
    p0 = d["peers"][0]
    assert "metrics" in p0
    assert p0["metrics"]["PE"] is not None
    assert p0["metrics"]["ROE"] is not None
    assert p0["metrics"]["PB"] is None  # 没填


@pytest.mark.asyncio
async def test_peers_fallback_to_industry_l1(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """同 l2 数量不够时, 用 l1 兜底凑齐 limit."""
    async with session_factory() as session:
        session.add(
            _make_ipo(
                code="0700.HK",
                name="腾讯控股",
                industry_l1="互联网",
                industry_l2="社交平台",
            )
        )
        # 只有 1 只同 l2, 不够 limit=3
        session.add(
            _make_ipo(
                code="3690.HK",
                name="PEER_L2",
                industry_l1="互联网",
                industry_l2="社交平台",
                listing_date=date(2024, 6, 1),
            )
        )
        # 同 l1 不同 l2, 用于 fallback
        session.add(
            _make_ipo(
                code="9988.HK",
                name="PEER_L1A",
                industry_l1="互联网",
                industry_l2="电商",
                listing_date=date(2024, 5, 1),
            )
        )
        session.add(
            _make_ipo(
                code="JD.HK",
                name="PEER_L1B",
                industry_l1="互联网",
                industry_l2="电商",
                listing_date=date(2024, 4, 1),
            )
        )
        await session.commit()

    tool = get("get_peer_comparison")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK", "limit": 3})

    assert r.ok is True, r.error
    assert r.data is not None
    peer_codes = [p["code"] for p in r.data["peers"]]
    assert len(peer_codes) == 3
    # l2 命中先来, l1 fallback 补齐
    assert peer_codes[0] == "3690.HK"
    assert {"9988.HK", "JD.HK"}.issubset(set(peer_codes))


@pytest.mark.asyncio
async def test_peers_no_match_warns(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """该行业第一只 IPO, 没有可比 → success + 空 peers + warning."""
    async with session_factory() as session:
        session.add(
            _make_ipo(
                code="UNIQUE.HK",
                name="孤独的新行业",
                industry_l1="量子计算",
                industry_l2="光量子",
            )
        )
        await session.commit()

    tool = get("get_peer_comparison")
    assert tool is not None
    r = await tool.runner({"code": "UNIQUE.HK"})

    assert r.ok is True, r.error
    assert r.data is not None
    assert r.data["peer_count"] == 0
    assert r.data["peers"] == []
    assert "warning" in r.data
    assert "未在 ipos 表" in r.data["warning"]


@pytest.mark.asyncio
async def test_peers_target_not_found_returns_failure(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    tool = get("get_peer_comparison")
    assert tool is not None
    r = await tool.runner({"code": "9999.HK"})
    assert r.ok is False
    assert r.error is not None
    assert "未找到" in r.error


@pytest.mark.asyncio
async def test_peers_custom_dimensions_subset(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    async with session_factory() as session:
        session.add(_make_ipo(code="0700.HK", name="A", industry_l2="X"))
        session.add(
            _make_ipo(
                code="3690.HK",
                name="PEER",
                industry_l2="X",
                pe_ratio=Decimal("18.0"),
                extra={"financial_summary": {"revenue": 999.0, "roe": 0.20}},
            )
        )
        await session.commit()

    tool = get("get_peer_comparison")
    assert tool is not None
    r = await tool.runner(
        {"code": "0700.HK", "dimensions": ["PE", "Revenue"]}
    )
    assert r.ok is True, r.error
    assert r.data is not None
    assert r.data["dimensions"] == ["PE", "Revenue"]
    metrics = r.data["peers"][0]["metrics"]
    assert set(metrics.keys()) == {"PE", "Revenue"}
    assert metrics["PE"] == pytest.approx(18.0)
    assert metrics["Revenue"] == pytest.approx(999.0)


@pytest.mark.asyncio
async def test_peers_invalid_dimension_rejected() -> None:
    """Literal 校验: 不在 enum 的 dim 名拒收."""
    tool = get("get_peer_comparison")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK", "dimensions": ["NotADimension"]})
    assert r.ok is False
    assert r.error is not None
    assert "参数校验失败" in r.error


@pytest.mark.asyncio
async def test_peers_validation_short_code() -> None:
    tool = get("get_peer_comparison")
    assert tool is not None
    r = await tool.runner({"code": "abc"})
    assert r.ok is False


@pytest.mark.asyncio
async def test_peers_limit_clamped_to_ge_1_le_10() -> None:
    tool = get("get_peer_comparison")
    assert tool is not None
    r1 = await tool.runner({"code": "0700.HK", "limit": 0})
    assert r1.ok is False
    r2 = await tool.runner({"code": "0700.HK", "limit": 999})
    assert r2.ok is False


def test_peers_openai_schema_shape() -> None:
    tool = get("get_peer_comparison")
    assert tool is not None
    schema = tool.to_openai_schema()
    fn = schema["function"]
    assert fn["name"] == "get_peer_comparison"
    params = fn["parameters"]
    assert "code" in params["required"]
    # dimensions 是 array of literal, properties 里有 enum 限制
    assert "dimensions" in params["properties"]
    # title 应被剔除 (LLM 兼容)
    assert "title" not in params
