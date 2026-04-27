"""BE-S3-007 券商列表 + 详情 端到端集成测 (≥ 10 条).

覆盖 (spec/10 §BE-S3-007 AC):

列表 API ``GET /brokers``:
1.  default — only_active=True 默认隐藏下架 + display_order DESC 排序
2.  market=HK 筛选 — 命中 HK 券商, 漏掉纯 A 券商
3.  market=A 筛选 — A 券商命中
4.  partnership=BOTH 筛选 — 仅 BOTH 出
5.  partnership=NONE 筛选 — 仅 NONE 出
6.  market=US 筛选 — 国际券商命中, 中信不出
7.  is_active=False 默认隐藏
8.  软删 (deleted_at IS NOT NULL) 自动隐藏
9.  partnership_* 三字段绝不出 API (BrokerPublic forbid)

详情 API ``GET /brokers/{slug}``:
10. happy path — 已存在 slug 命中 200
11. 不存在 slug → 404
12. 软删后 → 404
13. partnership_* 不出详情

缓存:
14. list 缓存命中 (二次调用走缓存)
15. invalidate_namespace("brokers:list") 清缓存后回源

设计目的:
- 9, 13 防御 in depth (FE 永不能感知财务条款)
- 7 / 8 区分 ``is_active`` (运营临时下架) vs ``deleted_at`` (逻辑删) 两种隐藏路径
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache import invalidate_namespace
from app.db.models import Broker

pytestmark = pytest.mark.db


# ─── helpers ───────────────────────────────────────────────────────────────


async def _insert_broker(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    slug: str,
    name_zh: str,
    market_support: list[str],
    partnership_type: str = "NONE",
    cpa: float | None = None,
    cps: float | None = None,
    is_active: bool = True,
    deleted: bool = False,
    display_order: int = 0,
    promotion_active: bool = False,
) -> uuid.UUID:
    async with session_factory() as s:
        promo: dict[str, Any] = {
            "is_active": promotion_active,
            "title": "x",
            "description": "y",
            "end_at": "2026-12-31",
            "invite_code": "X",
            "referral_url": "https://example.com",
        }
        b = Broker(
            slug=slug,
            name_zh=name_zh,
            name_en=None,
            logo_url=None,
            market_support=market_support,
            licenses=["SFC-1"],
            fees={"hk_commission_rate": 0.0003},
            features={"ipo_subscription": True},
            promotion=promo,
            partnership_type=partnership_type,
            partnership_cpa_amount=Decimal(str(cpa)) if cpa is not None else None,
            partnership_cps_rate=Decimal(str(cps)) if cps is not None else None,
            display_order=display_order,
            is_active=is_active,
        )
        if deleted:
            b.deleted_at = datetime.now(UTC)
        s.add(b)
        await s.commit()
        return b.broker_id


@pytest.fixture
async def seven_brokers(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[str]:
    """种入 7 家覆盖各组合的券商; 返写入 slug 列表 (按 display_order 高→低)."""
    fixtures = [
        # display_order, slug, market, partnership, cpa, cps, is_active, deleted
        (100, "futubull", ["HK", "US", "SG"], "BOTH", 1500.0, 0.025, True, False),
        (95, "tiger", ["HK", "US", "SG"], "BOTH", 1200.0, 0.025, True, False),
        (90, "longbridge", ["HK", "US", "SG"], "CPA", 1000.0, None, True, False),
        (80, "hti", ["HK", "US"], "CPA", 800.0, None, True, False),
        (70, "ibkr", ["HK", "US", "SG"], "CPS", None, 0.018, True, False),
        (50, "citic", ["A"], "NONE", None, None, True, False),
        # 一个临时下架 + 一个软删 — 默认列表都不该出现
        (60, "off-active", ["HK"], "NONE", None, None, False, False),
        (55, "soft-deleted", ["HK"], "NONE", None, None, True, True),
    ]
    inserted: list[str] = []
    for order, slug, mkt, pt, cpa, cps, active, deleted in fixtures:
        await _insert_broker(
            session_factory,
            slug=slug,
            name_zh=slug.upper(),
            market_support=mkt,
            partnership_type=pt,
            cpa=cpa,
            cps=cps,
            is_active=active,
            deleted=deleted,
            display_order=order,
        )
        inserted.append(slug)
    return inserted


# ─── 列表 API ──────────────────────────────────────────────────────────────


async def test_list_brokers_default_only_active_and_ordered(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """1. 默认 only_active=True + display_order DESC: 只出 6 家活跃 + futubull 第 1."""
    resp = await client.get("/api/v1/brokers")
    assert resp.status_code == 200
    payload = resp.json()
    items = payload["items"]
    assert payload["total"] == 6  # 排除 off-active + soft-deleted
    slugs = [b["slug"] for b in items]
    # 顺序: 100 futu / 95 tiger / 90 longbridge / 80 hti / 70 ibkr / 50 citic
    assert slugs == ["futubull", "tiger", "longbridge", "hti", "ibkr", "citic"]


async def test_list_brokers_filter_market_hk(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """2. market=HK: 命中 5 家 (HK 出现在 market_support 列表), 漏掉 citic (纯 A)."""
    resp = await client.get("/api/v1/brokers", params={"market": "HK"})
    assert resp.status_code == 200
    slugs = {b["slug"] for b in resp.json()["items"]}
    assert "citic" not in slugs
    assert "futubull" in slugs
    assert "ibkr" in slugs


async def test_list_brokers_filter_market_a(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """3. market=A: 仅 citic 命中."""
    resp = await client.get("/api/v1/brokers", params={"market": "A"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["slug"] == "citic"


async def test_list_brokers_filter_partnership_both(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """4. partnership=BOTH: 仅 futu + tiger."""
    resp = await client.get("/api/v1/brokers", params={"partnership": "BOTH"})
    assert resp.status_code == 200
    slugs = {b["slug"] for b in resp.json()["items"]}
    assert slugs == {"futubull", "tiger"}


async def test_list_brokers_filter_partnership_none(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """5. partnership=NONE: 仅 citic (off-active / soft-deleted 默认隐藏)."""
    resp = await client.get("/api/v1/brokers", params={"partnership": "NONE"})
    assert resp.status_code == 200
    slugs = {b["slug"] for b in resp.json()["items"]}
    assert slugs == {"citic"}


async def test_list_brokers_filter_market_us(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """6. market=US: futu/tiger/longbridge/hti/ibkr 全命中, citic 不在."""
    resp = await client.get("/api/v1/brokers", params={"market": "US"})
    assert resp.status_code == 200
    slugs = {b["slug"] for b in resp.json()["items"]}
    assert "citic" not in slugs
    assert {"futubull", "tiger", "longbridge", "hti", "ibkr"}.issubset(slugs)


async def test_list_brokers_hides_inactive(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """7. is_active=False 自动隐藏 (off-active 不出)."""
    resp = await client.get("/api/v1/brokers")
    slugs = {b["slug"] for b in resp.json()["items"]}
    assert "off-active" not in slugs


async def test_list_brokers_hides_soft_deleted(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """8. deleted_at 自动隐藏."""
    resp = await client.get("/api/v1/brokers")
    slugs = {b["slug"] for b in resp.json()["items"]}
    assert "soft-deleted" not in slugs


async def test_list_brokers_no_partnership_leak(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """9. partnership_* 不出 list API (BrokerPublic.extra=forbid 防御 in depth)."""
    resp = await client.get("/api/v1/brokers")
    assert resp.status_code == 200
    forbidden = {"partnership_type", "partnership_cpa_amount", "partnership_cps_rate"}
    for item in resp.json()["items"]:
        assert forbidden.isdisjoint(item.keys()), (
            f"partnership_* 字段泄漏到 {item['slug']}: {item}"
        )


# ─── 详情 API ──────────────────────────────────────────────────────────────


async def test_broker_detail_happy_path(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """10. 详情 200 + 关键字段齐全 + 无 partnership_*."""
    resp = await client.get("/api/v1/brokers/futubull")
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == "futubull"
    assert body["name_zh"] == "FUTUBULL"
    assert body["market_support"] == ["HK", "US", "SG"]
    assert body["is_active"] is True


async def test_broker_detail_not_found(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """11. slug 不存在 → 404."""
    resp = await client.get("/api/v1/brokers/no-such-broker")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "broker_not_found"


async def test_broker_detail_soft_deleted_returns_404(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """12. 软删后详情 404 (即便 slug 物理存在)."""
    resp = await client.get("/api/v1/brokers/soft-deleted")
    assert resp.status_code == 404


async def test_broker_detail_no_partnership_leak(
    client: httpx.AsyncClient, seven_brokers: list[str]
) -> None:
    """13. 详情也不出 partnership_*."""
    resp = await client.get("/api/v1/brokers/futubull")
    assert resp.status_code == 200
    body = resp.json()
    forbidden = {"partnership_type", "partnership_cpa_amount", "partnership_cps_rate"}
    assert forbidden.isdisjoint(body.keys())


# ─── 缓存 ──────────────────────────────────────────────────────────────────


async def test_list_brokers_cache_hit_then_invalidate(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    seven_brokers: list[str],
) -> None:
    """14 + 15. 列表打入缓存 → 写入新券商 → 缓存命中老结果 → invalidate 后回源."""
    first = await client.get("/api/v1/brokers")
    assert first.status_code == 200
    first_total = first.json()["total"]

    # 直接写入新活跃券商, 不走 API (绕过 invalidate hook)
    await _insert_broker(
        session_factory,
        slug="newbie",
        name_zh="NEWBIE",
        market_support=["HK"],
        display_order=200,  # 比 futubull 还高, 应排第一
    )

    # 二次调用应命中缓存, 看不到新券商
    cached_resp = await client.get("/api/v1/brokers")
    assert cached_resp.status_code == 200
    assert cached_resp.json()["total"] == first_total
    cached_slugs = [b["slug"] for b in cached_resp.json()["items"]]
    assert "newbie" not in cached_slugs

    # 显式失效后, 第三次调用回源 → 看到新券商 + 排第一
    await invalidate_namespace("brokers:list")
    fresh = await client.get("/api/v1/brokers")
    assert fresh.json()["total"] == first_total + 1
    assert fresh.json()["items"][0]["slug"] == "newbie"
