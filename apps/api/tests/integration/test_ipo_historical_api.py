"""BE-S4-003 集成测试: 历史 IPO 筛选 + 行业聚合 API.

覆盖矩阵 (10 条):
1. test_historical_default_returns_listed_only
   ``GET /ipos/historical`` 默认仅返 status='listed' 行 (filter 出 upcoming/withdrawn)
2. test_historical_market_filter
   ``market=HK`` 只返 HK 行; ``market=A`` 只返 A 行
3. test_historical_industry_filter
   ``industry=互联网`` 仅返 industry_l1='互联网' 行
4. test_historical_year_range_filter
   ``year_from=2022&year_to=2023`` 仅返 listing_date 落在区间内
5. test_historical_sponsor_jsonb_filter
   ``sponsor=中金公司`` 命中 sponsors JSONB 含该元素的行
6. test_historical_sort_by_first_day_change
   ``sort_by=first_day_change_pct`` 第一行 first_day 最大 (DESC NULLS LAST)
7. test_historical_invalid_year_range_400
   ``year_from > year_to`` 返 400 + invalid_year_range
8. test_peer_aggregate_happy
   行业 ≥ 5 篇 → percentile / scatter_points 完整, 含 self high-light
9. test_peer_aggregate_insufficient_data
   行业 < 5 篇 → stats 全 None + scatter_points=[]
10. test_peer_aggregate_unknown_code_404
    code 不存在 → 404 + ipo_or_industry_missing

不验:
- @cached 命中性 (单测覆盖足够; e2e 不要依赖缓存读数, fixture 起点是 truncate_all 空表)
- BE-S4-002 backfill 数据互动 (本 PR 自己 seed 测试数据, 不依赖 backfill 脚本)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import IPO

pytestmark = pytest.mark.db


# ─── seed helper ────────────────────────────────────────────────────


async def _seed_test_ipos(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """种 ~15 条历史 IPO 测试数据: 多市场 / 多行业 / 多年份 / 多 sponsor / 多 status.

    覆盖:
    - 互联网: 6 行 (HK 4, A 2; 含 5 个 listed 满足 peer_count ≥ 5)
    - 医药: 2 行 (HK; listed) — 不足 5, 触发 insufficient_data 兜底
    - 新能源: 3 行 (HK 2, A 1; listed) — 不足 5
    - 1 行 status='upcoming' 验证 listed-only 路径
    - 1 行 status='withdrawn' 验证不被列出
    """
    rows = [
        # 互联网 (5 listed + 1 upcoming = 6 行, 满足 ≥ 5 peer_count)
        IPO(
            code="00700.HK", name="腾讯控股", market="HK",
            industry_l1="互联网", industry_l2="社交",
            issue_price=Decimal("3.7"), issue_currency="HKD",
            listing_date=date(2004, 6, 16),
            raised_amount=Decimal("1545000000"),
            pe_ratio=Decimal("16.5"),
            first_day_change_pct=Decimal("13.5"),
            one_lot_winning_rate=Decimal("0.7"),
            oversubscribe_multiple=Decimal("158.2"),
            sponsors=["高盛", "中金公司"],
            status="listed",
            data_source="backfill-test",
        ),
        IPO(
            code="03690.HK", name="美团", market="HK",
            industry_l1="互联网", industry_l2="本地生活",
            issue_price=Decimal("69.0"), issue_currency="HKD",
            listing_date=date(2018, 9, 20),
            pe_ratio=Decimal("28.0"),
            first_day_change_pct=Decimal("5.29"),
            one_lot_winning_rate=Decimal("0.38"),
            sponsors=["美林", "高盛"],
            status="listed",
            data_source="backfill-test",
        ),
        IPO(
            code="01024.HK", name="快手", market="HK",
            industry_l1="互联网", industry_l2="短视频",
            issue_price=Decimal("115.0"), issue_currency="HKD",
            listing_date=date(2022, 2, 5),
            pe_ratio=Decimal("32.0"),
            first_day_change_pct=Decimal("160.87"),
            sponsors=["摩根士丹利"],
            status="listed",
            data_source="backfill-test",
        ),
        IPO(
            code="09618.HK", name="京东", market="HK",
            industry_l1="互联网", industry_l2="电商",
            issue_price=Decimal("226.0"), issue_currency="HKD",
            listing_date=date(2023, 6, 18),
            pe_ratio=Decimal("18.5"),
            first_day_change_pct=Decimal("3.54"),
            sponsors=["美林", "中金公司"],
            status="listed",
            data_source="backfill-test",
        ),
        IPO(
            code="600519.SH", name="贵州茅台-A", market="A",
            industry_l1="互联网", industry_l2="社交",  # 强行归 互联网 凑 ≥ 5
            issue_price=Decimal("31.39"), issue_currency="CNY",
            listing_date=date(2022, 8, 27),
            pe_ratio=Decimal("22.0"),
            first_day_change_pct=Decimal("5.44"),
            sponsors=["申银万国"],
            status="listed",
            data_source="backfill-test",
        ),
        IPO(
            code="00100.HK", name="互联网 upcoming", market="HK",
            industry_l1="互联网",
            issue_price=Decimal("10.0"), issue_currency="HKD",
            sponsors=["中金公司"],
            status="upcoming",  # 不应出现在 historical 列表
            data_source="backfill-test",
        ),
        # 医药 (HK, 仅 2 行, peer_count<5)
        IPO(
            code="06160.HK", name="百济神州", market="HK",
            industry_l1="医药", industry_l2="创新药",
            listing_date=date(2018, 8, 8),
            first_day_change_pct=Decimal("-5.0"),
            sponsors=["高盛"],
            status="listed",
            data_source="backfill-test",
        ),
        IPO(
            code="06618.HK", name="京东健康", market="HK",
            industry_l1="医药", industry_l2="互联网医疗",
            listing_date=date(2020, 12, 8),
            first_day_change_pct=Decimal("55.85"),
            sponsors=["中金公司"],
            status="listed",
            data_source="backfill-test",
        ),
        # 新能源 (3 行, 仍 < 5)
        IPO(
            code="01211.HK", name="比亚迪股份", market="HK",
            industry_l1="新能源", industry_l2="新能源车",
            listing_date=date(2023, 7, 31),
            first_day_change_pct=Decimal("38.35"),
            sponsors=["瑞银"],
            status="listed",
            data_source="backfill-test",
        ),
        IPO(
            code="09866.HK", name="蔚来", market="HK",
            industry_l1="新能源", industry_l2="新能源车",
            listing_date=date(2022, 3, 10),
            first_day_change_pct=Decimal("-1.65"),
            sponsors=["摩根士丹利"],
            status="listed",
            data_source="backfill-test",
        ),
        IPO(
            code="002594.SZ", name="比亚迪", market="A",
            industry_l1="新能源", industry_l2="新能源车",
            listing_date=date(2021, 6, 30),
            first_day_change_pct=Decimal("40.73"),
            sponsors=["瑞银证券"],
            status="listed",
            data_source="backfill-test",
        ),
        # withdrawn (不应出现)
        IPO(
            code="00999.HK", name="撤回 IPO", market="HK",
            industry_l1="互联网",
            listing_date=date(2022, 1, 1),
            status="withdrawn",
            data_source="backfill-test",
        ),
    ]
    async with session_factory() as s:
        s.add_all(rows)
        await s.commit()


# ─── 1 ~ 7. /ipos/historical ────────────────────────────────────────


async def test_historical_default_returns_listed_only(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_test_ipos(session_factory)
    r = await client.get("/api/v1/ipos/historical?size=50")
    assert r.status_code == 200
    body = r.json()
    statuses = {it["status"] for it in body["items"]}
    assert statuses == {"listed"}, f"应仅含 listed, 实际 {statuses}"
    # upcoming + withdrawn 应被 filter 掉
    codes = {it["code"] for it in body["items"]}
    assert "00100.HK" not in codes  # upcoming
    assert "00999.HK" not in codes  # withdrawn


async def test_historical_market_filter(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_test_ipos(session_factory)
    r_hk = await client.get("/api/v1/ipos/historical?market=HK&size=50")
    r_a = await client.get("/api/v1/ipos/historical?market=A&size=50")
    assert r_hk.status_code == 200
    assert r_a.status_code == 200
    hk_markets = {it["market"] for it in r_hk.json()["items"]}
    a_markets = {it["market"] for it in r_a.json()["items"]}
    assert hk_markets == {"HK"}
    assert a_markets == {"A"}


async def test_historical_industry_filter(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_test_ipos(session_factory)
    r = await client.get("/api/v1/ipos/historical?industry=医药&size=50")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2  # 百济神州 + 京东健康
    assert {it["industry"] for it in body["items"]} == {"医药"}


async def test_historical_year_range_filter(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_test_ipos(session_factory)
    r = await client.get(
        "/api/v1/ipos/historical?year_from=2022&year_to=2022&size=50"
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    for it in items:
        ld = it["listing_date"]
        assert ld is not None and ld.startswith("2022-"), f"年份越界: {ld}"


async def test_historical_sponsor_jsonb_filter(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """sponsor='中金公司' 命中 sponsors JSONB 数组含该元素的行."""
    await _seed_test_ipos(session_factory)
    r = await client.get("/api/v1/ipos/historical?sponsor=中金公司&size=50")
    assert r.status_code == 200
    items = r.json()["items"]
    # 腾讯 / 京东 / 京东健康 都含中金公司, 至少 ≥ 3 行
    assert len(items) >= 3
    for it in items:
        assert "中金公司" in (it["sponsors"] or [])


async def test_historical_sort_by_first_day_change(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """sort_by=first_day_change_pct → 第一行最高涨幅 (快手 +160.87%)."""
    await _seed_test_ipos(session_factory)
    r = await client.get(
        "/api/v1/ipos/historical?sort_by=first_day_change_pct&size=5"
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    # 快手 1024 应排第一
    assert items[0]["code"] == "01024.HK"
    assert float(items[0]["first_day_change_pct"]) == pytest.approx(160.87, abs=0.01)


async def test_historical_invalid_year_range_400(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get(
        "/api/v1/ipos/historical?year_from=2025&year_to=2022"
    )
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["code"] == "invalid_year_range"


# ─── 8 ~ 10. /ipos/{code}/peer-aggregate ────────────────────────────


async def test_peer_aggregate_happy(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """互联网行业 ≥ 5 listed → percentile / scatter / self high-light 完整."""
    await _seed_test_ipos(session_factory)
    r = await client.get("/api/v1/ipos/00700.HK/peer-aggregate")
    assert r.status_code == 200
    body = r.json()

    assert body["code"] == "00700.HK"
    assert body["industry_l1"] == "互联网"
    assert body["peer_count"] >= 5

    # first_day_change_pct stats 应有真值 (不是全 None 兜底)
    fd = body["first_day_change_pct"]
    assert fd["mean"] is not None
    assert fd["median"] is not None
    assert fd["min"] is not None
    assert fd["max"] is not None
    # min/max 范围合理 (我们 seed 的数据 [3.54, 160.87])
    assert -10 <= fd["min"] <= fd["max"] <= 200

    # 散点图 dot 含 self
    points = body["scatter_points"]
    assert len(points) >= 5
    self_points = [p for p in points if p["is_self"]]
    assert len(self_points) == 1
    assert self_points[0]["code"] == "00700.HK"


async def test_peer_aggregate_insufficient_data(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """新能源仅 3 行 < 5 → stats 全 None + scatter_points=[]."""
    await _seed_test_ipos(session_factory)
    r = await client.get("/api/v1/ipos/01211.HK/peer-aggregate")
    assert r.status_code == 200
    body = r.json()

    assert body["industry_l1"] == "新能源"
    assert body["peer_count"] < 5

    fd = body["first_day_change_pct"]
    assert fd["mean"] is None
    assert fd["median"] is None

    assert body["scatter_points"] == []


async def test_peer_aggregate_unknown_code_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """code 不存在 → 404 + ipo_or_industry_missing."""
    await _seed_test_ipos(session_factory)
    r = await client.get("/api/v1/ipos/99999.HK/peer-aggregate")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "ipo_or_industry_missing"


_ = (Any,)  # 防 ruff 误删未来扩展用
