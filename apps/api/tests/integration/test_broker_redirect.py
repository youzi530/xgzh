"""BE-S3-008 broker redirect / stats / postback 端到端集成测.

覆盖 (spec/10 §BE-S3-008 AC + 防御性反向用例):

redirect 端点 ``GET /brokers/{slug}/redirect``:
1.  happy path — 302 + Location 拼了 utm_source=xgzh + utm_campaign + invite_code
2.  匿名 + device_id → 落 1 行 click (user_id IS NULL)
3.  登录 (Bearer) → 落 1 行 click (user_id 非空)
4.  同 device_id 同 utm_campaign 1h 内重复点击 → 仅落 1 行 (Redis 防刷)
5.  防刷命中后 redirect 仍然 302 (UX > 数据完整)
6.  不同 device_id 同 utm_campaign 同时打 → 各落 1 行
7.  slug 不存在 → 404 ``broker_not_found``
8.  ``promotion.is_active=False`` → 404 ``broker_promotion_inactive``
9.  ``is_active=False`` (运营临时下架) → 404 (走 active 通路过滤)
10. referral_url 已自带 utm_source → 不被我方 utm_source=xgzh 覆盖

stats 端点 ``GET /brokers/{slug}/stats``:
11. 未登录 → 401
12. 登录 + 5 种 event_type 都种入 → 计数 + total_amount_cny 准确

postback 端点 ``POST /brokers/postback``:
13. 任意合法 PostbackRequest body → 501 (Sprint 4+ 占位)

设计目的:
- 5 / 6 验证防刷 key 的隔离粒度对 (broker, actor, utm_campaign)
- 8 / 9 区分 promotion 推广位下线 vs broker 主体下架
- 12 验证 ``attributed=False`` 的 amount 不计入 ``total_amount_cny`` (财务对账隔离)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Broker, ConversionEvent, User
from app.security.jwt import create_access_token

pytestmark = pytest.mark.db


# ─── helpers ───────────────────────────────────────────────────────────────


async def _insert_active_broker(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    slug: str = "futubull",
    promotion_active: bool = True,
    referral_url: str = "https://www.futunn.com/sg/account-open",
    is_active: bool = True,
    deleted: bool = False,
    invite_code: str = "XGZH-FUTU",
) -> uuid.UUID:
    async with session_factory() as s:
        promo: dict[str, Any] = {
            "is_active": promotion_active,
            "title": "x",
            "description": "y",
            "end_at": "2026-12-31",
            "invite_code": invite_code,
            "referral_url": referral_url,
        }
        b = Broker(
            slug=slug,
            name_zh=slug.upper(),
            name_en=None,
            logo_url=None,
            market_support=["HK", "US"],
            licenses=["SFC-1"],
            fees={"hk_commission_rate": 0.0003},
            features={"ipo_subscription": True},
            promotion=promo,
            partnership_type="BOTH",
            partnership_cpa_amount=Decimal("1500.00"),
            partnership_cps_rate=Decimal("0.025"),
            display_order=100,
            is_active=is_active,
        )
        if deleted:
            b.deleted_at = datetime.now(UTC)
        s.add(b)
        await s.commit()
        return b.broker_id


async def _seed_user_and_token(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    phone_suffix: str = "0001",
) -> tuple[uuid.UUID, str]:
    async with session_factory() as s:
        u = User(
            phone=f"+8613800{phone_suffix}",
            invite_code=f"TST{phone_suffix}",
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        uid = u.user_id
    token, _ = create_access_token(user_id=uid)
    return uid, token


async def _count_click_events(
    session_factory: async_sessionmaker[AsyncSession], broker_id: uuid.UUID
) -> int:
    from sqlalchemy import func, select

    async with session_factory() as s:
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(ConversionEvent)
                    .where(
                        ConversionEvent.broker_id == broker_id,
                        ConversionEvent.event_type == "click",
                    )
                )
            ).scalar_one()
        )


async def _fetch_click_events(
    session_factory: async_sessionmaker[AsyncSession], broker_id: uuid.UUID
) -> list[ConversionEvent]:
    from sqlalchemy import select

    async with session_factory() as s:
        rows = (
            await s.execute(
                select(ConversionEvent)
                .where(
                    ConversionEvent.broker_id == broker_id,
                    ConversionEvent.event_type == "click",
                )
                .order_by(ConversionEvent.created_at.asc())
            )
        ).scalars().all()
        return list(rows)


# ─── 1. happy path ────────────────────────────────────────────────────────


async def test_redirect_happy_path_302_with_utm(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """1. 302 + Location 拼了 utm_source=xgzh + utm_campaign + invite_code."""
    await _insert_active_broker(session_factory)

    resp = await client.get(
        "/api/v1/brokers/futubull/redirect",
        params={
            "utm_campaign": "ipo-202604",
            "utm_medium": "compare-page",
            "device_id": "dev-A",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    parsed = urlparse(location)
    assert parsed.netloc == "www.futunn.com"
    qs = parse_qs(parsed.query)
    assert qs["utm_source"] == ["xgzh"]
    assert qs["utm_campaign"] == ["ipo-202604"]
    assert qs["utm_medium"] == ["compare-page"]
    assert qs["invite_code"] == ["XGZH-FUTU"]


# ─── 2. 匿名落库 ───────────────────────────────────────────────────────────


async def test_redirect_anonymous_writes_event_with_null_user(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """2. 匿名 + device_id → 落 1 行 click (user_id IS NULL)."""
    bid = await _insert_active_broker(session_factory, slug="tiger")

    resp = await client.get(
        "/api/v1/brokers/tiger/redirect",
        params={"utm_campaign": "anon-test", "device_id": "dev-anon"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    rows = await _fetch_click_events(session_factory, bid)
    assert len(rows) == 1
    assert rows[0].user_id is None
    assert rows[0].device_id == "dev-anon"
    assert rows[0].utm_campaign == "anon-test"
    assert rows[0].utm_source == "xgzh"
    assert rows[0].attributed is False


# ─── 3. 登录落库 ───────────────────────────────────────────────────────────


async def test_redirect_authenticated_writes_event_with_user_id(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """3. 登录 (Bearer) → 落 1 行 click (user_id 非空)."""
    bid = await _insert_active_broker(session_factory, slug="longbridge")
    uid, token = await _seed_user_and_token(session_factory, phone_suffix="0010")

    resp = await client.get(
        "/api/v1/brokers/longbridge/redirect",
        params={"utm_campaign": "auth-test", "device_id": "dev-auth"},
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    rows = await _fetch_click_events(session_factory, bid)
    assert len(rows) == 1
    assert rows[0].user_id == uid
    assert rows[0].device_id == "dev-auth"


# ─── 4. 同设备同 utm 1h 内防刷 ────────────────────────────────────────────


async def test_redirect_dedup_same_device_within_1h(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """4. 同 device_id 同 utm_campaign 1h 内重复点击 → 仅落 1 行."""
    bid = await _insert_active_broker(session_factory, slug="ibkr")

    for _ in range(3):
        resp = await client.get(
            "/api/v1/brokers/ibkr/redirect",
            params={"utm_campaign": "dedup-test", "device_id": "dev-dup"},
            follow_redirects=False,
        )
        # 5. 防刷命中后 redirect 仍然 302 (UX > 数据完整)
        assert resp.status_code == 302

    assert await _count_click_events(session_factory, bid) == 1


# ─── 6. 不同 device_id 各落各 ─────────────────────────────────────────────


async def test_redirect_different_device_different_rows(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """6. 不同 device_id 同 utm_campaign 同时打 → 各落 1 行."""
    bid = await _insert_active_broker(session_factory, slug="hti")

    for did in ("dev-1", "dev-2", "dev-3"):
        resp = await client.get(
            "/api/v1/brokers/hti/redirect",
            params={"utm_campaign": "multi-dev", "device_id": did},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    assert await _count_click_events(session_factory, bid) == 3


# ─── 7. unknown slug → 404 ────────────────────────────────────────────────


async def test_redirect_unknown_slug_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """7. slug 不存在 → 404 broker_not_found."""
    resp = await client.get(
        "/api/v1/brokers/no-such-broker/redirect",
        params={"device_id": "dev-X"},
        follow_redirects=False,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "broker_not_found"


# ─── 8. promotion 未启用 → 404 ────────────────────────────────────────────


async def test_redirect_promotion_inactive_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """8. promotion.is_active=False → 404 broker_promotion_inactive."""
    await _insert_active_broker(
        session_factory, slug="snowbull", promotion_active=False
    )

    resp = await client.get(
        "/api/v1/brokers/snowbull/redirect",
        params={"device_id": "dev-Y"},
        follow_redirects=False,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "broker_promotion_inactive"


# ─── 9. broker is_active=False / 软删 → 404 ─────────────────────────────────


async def test_redirect_broker_inactive_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """9. is_active=False → 404 (走 active 通路过滤掉)."""
    await _insert_active_broker(
        session_factory, slug="off-broker", is_active=False
    )

    resp = await client.get(
        "/api/v1/brokers/off-broker/redirect",
        params={"device_id": "dev-Z"},
        follow_redirects=False,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "broker_not_found"


# ─── 10. referral_url 自带 utm_source 不被覆盖 ────────────────────────────


async def test_redirect_preserves_existing_utm_source(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """10. referral_url 已自带 ?utm_source=internal → 不被我方 utm_source=xgzh 覆盖.

    防 utm_campaign='&utm_source=evil' 注入既有参数 (urlencode 防御); 同时尊重
    broker BD 自定义的 utm_source (例如券商方对 XGZH 渠道有专门的 source 标记).
    """
    await _insert_active_broker(
        session_factory,
        slug="custom-utm",
        referral_url="https://broker.example.com/open?utm_source=fixed-by-broker",
    )

    resp = await client.get(
        "/api/v1/brokers/custom-utm/redirect",
        params={"utm_campaign": "x", "device_id": "dev-M"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    qs = parse_qs(urlparse(resp.headers["location"]).query)
    assert qs["utm_source"] == ["fixed-by-broker"]  # 不被 xgzh 覆盖
    assert qs["utm_campaign"] == ["x"]


# ─── 11. stats 未登录 → 401 ────────────────────────────────────────────────


async def test_stats_requires_auth(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """11. 未登录调 stats → 401 (token_missing)."""
    await _insert_active_broker(session_factory, slug="auth-test-broker")

    resp = await client.get("/api/v1/brokers/auth-test-broker/stats")
    assert resp.status_code == 401


# ─── 12. stats happy path ─────────────────────────────────────────────────


async def test_stats_30d_groups_by_event_type(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """12. 落 5 类事件 + auth → 计数 + total_amount_cny (仅 attributed=True 计入).

    手动种入 5 类事件 (跳过 redirect 端点, 直接走 ORM, 因为 stats 不依赖 redirect 的
    防刷流程); 验证 stats 端点 GROUP BY 计数 + 累计入金 (attributed 隔离).
    """
    bid = await _insert_active_broker(session_factory, slug="stats-broker")
    _, token = await _seed_user_and_token(session_factory, phone_suffix="0011")

    async with session_factory() as s:
        # clicks: 2 (一定不计入 amount 因 click event_type 不带 amount)
        s.add_all(
            [
                ConversionEvent(
                    broker_id=bid,
                    device_id="d1",
                    event_type="click",
                    attributed=False,
                ),
                ConversionEvent(
                    broker_id=bid,
                    device_id="d2",
                    event_type="click",
                    attributed=False,
                ),
                # signups: 1 (attributed=False, amount 不计入)
                ConversionEvent(
                    broker_id=bid,
                    device_id="d3",
                    event_type="signup",
                    amount_cny=Decimal("1000"),
                    attributed=False,
                ),
                # kyc_pass: 1
                ConversionEvent(
                    broker_id=bid,
                    device_id="d4",
                    event_type="kyc_pass",
                    attributed=True,
                ),
                # deposits: 1 (attributed=True, amount 计入)
                ConversionEvent(
                    broker_id=bid,
                    device_id="d5",
                    event_type="deposit",
                    amount_cny=Decimal("50000"),
                    attributed=True,
                ),
                # first_trades: 1 (attributed=True, amount 计入)
                ConversionEvent(
                    broker_id=bid,
                    device_id="d6",
                    event_type="first_trade",
                    amount_cny=Decimal("3000"),
                    attributed=True,
                ),
            ]
        )
        await s.commit()

    resp = await client.get(
        "/api/v1/brokers/stats-broker/stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["slug"] == "stats-broker"
    assert j["broker_id"] == str(bid)
    assert j["window_days"] == 30
    assert j["clicks"] == 2
    assert j["signups"] == 1
    assert j["kyc_pass"] == 1
    assert j["deposits"] == 1
    assert j["first_trades"] == 1
    # 仅 attributed=True 的 deposit (50000) + first_trade (3000) 计入; signup 1000 不计入
    assert j["total_amount_cny"] == 53000.0


# ─── 13. postback → 501 ────────────────────────────────────────────────────


async def test_postback_returns_501(client: httpx.AsyncClient) -> None:
    """13. POST /brokers/postback (合法 body) → 501 占位."""
    resp = await client.post(
        "/api/v1/brokers/postback",
        json={
            "broker_slug": "futubull",
            "external_event_id": "evt-1",
            "event_type": "signup",
            "user_external_id": "broker-uid-1",
        },
    )
    assert resp.status_code == 501
    assert resp.json()["detail"]["code"] == "postback_not_implemented"


# ─── 14. dedup key 区分 utm_campaign ──────────────────────────────────────


async def test_redirect_dedup_per_utm_campaign(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """14. 同 device_id 不同 utm_campaign → 各落各 (dedup key 含 utm_campaign).

    设计目的: 同一用户在不同渠道 (官网 vs 推送 vs IPO 详情) 各点 1 次, 应都计入,
    才能让运营做渠道归因; 否则点 1 次后, 当天另一渠道流量全被防刷误杀.
    """
    bid = await _insert_active_broker(session_factory, slug="multi-utm-broker")

    for camp in ("home-banner", "ipo-detail", "compare-table"):
        resp = await client.get(
            "/api/v1/brokers/multi-utm-broker/redirect",
            params={"utm_campaign": camp, "device_id": "dev-same"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    assert await _count_click_events(session_factory, bid) == 3
