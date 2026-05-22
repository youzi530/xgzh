"""Sprint 11 BE-S11-A05: /admin/brokers 6 endpoint 端到端测试.

覆盖:
- RBAC: 401 (无 token) / 403 (非 admin)
- GET /admin/brokers — 列表 (含/不含 软删, 含/不含 下架)
- GET /admin/brokers/{slug} — 详情, 含 partnership_*, 含软删
- POST /admin/brokers — 新建 ok, slug 冲突 409, 字段校验 422
- PATCH /admin/brokers/{slug} — 标量 set, JSONB merge, 不存在 404
- DELETE /admin/brokers/{slug} — 软删 + 幂等 (重复 DELETE 仍 204)
- POST /admin/brokers/{slug}/restore — 恢复 + 幂等
- 数据完整性: GET /brokers/{slug}/redirect 优先用顶层 open_account_url
- 公开 list 不泄漏 partnership_* / 软删字段

放 ``tests/integration/`` 是因为本测试要走 ``patch_session_factory`` fixture
(broker_service.list_brokers / conversion_service 用 module-level ``get_session_factory()``,
不走 FastAPI Depends, 全局 lru_cache 必须 monkey-patch 才能拉到测试库).
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Broker
from app.services import otp_service

pytestmark = pytest.mark.db


# ─── helpers ──────────────────────────────────────────────────────


async def _login(
    c: httpx.AsyncClient, phone: str, code: str = "123456"
) -> dict:
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    r = await c.post("/api/v1/auth/login/phone", json={"phone": phone, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin_setup(c: httpx.AsyncClient) -> tuple[str, str]:
    """注册 admin (13007458553, 自动 is_admin=true) + 普通用户; 返 (admin, regular)."""
    admin_body = await _login(c, phone="+8613007458553")
    assert admin_body["user"]["is_admin"] is True, (
        "13007458553 必须自动是 admin (auth_service._maybe_grant_initial_admin)"
    )
    regular_body = await _login(c, phone="+8613800138000")
    assert regular_body["user"]["is_admin"] is False
    return admin_body["tokens"]["access_token"], regular_body["tokens"]["access_token"]


def _broker_payload(slug: str = "futubull", **overrides) -> dict:
    base = {
        "slug": slug,
        "name_zh": "富途牛牛",
        "name_en": "Futu",
        "logo_url": "https://example.com/futu.png",
        "open_account_url": "https://example.com/futu/open?ref=xgzh",
        "market_support": ["HK", "US"],
        "licenses": ["SFC-1"],
        "fees": {"hk_commission_rate": 0.0003},
        "features": {"ipo_subscription": True},
        "promotion": {
            "is_active": True,
            "title": "新用户开户送 100 美元",
            "referral_url": "https://example.com/futu/legacy",
        },
        "partnership_type": "CPA",
        "partnership_cpa_amount": "150.00",
        "display_order": 100,
        "is_active": True,
    }
    base.update(overrides)
    return base


# ─── 1. RBAC ──────────────────────────────────────────────────────


async def test_unauthenticated_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/admin/brokers")
    assert r.status_code == 401


async def test_non_admin_returns_403(client: httpx.AsyncClient) -> None:
    _admin, regular = await _admin_setup(client)
    r = await client.get("/api/v1/admin/brokers", headers=_bearer(regular))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "admin_required"


# ─── 2. POST create ──────────────────────────────────────────────


async def test_create_broker_success(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    r = await client.post(
        "/api/v1/admin/brokers",
        headers=_bearer(admin),
        json=_broker_payload(),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "futubull"
    assert body["name_zh"] == "富途牛牛"
    assert body["open_account_url"] == "https://example.com/futu/open?ref=xgzh"
    assert body["partnership_type"] == "CPA"
    assert body["is_deleted"] is False
    assert body["deleted_at"] is None


async def test_create_broker_slug_taken_returns_409(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers", headers=_bearer(admin), json=_broker_payload()
    )
    r = await client.post(
        "/api/v1/admin/brokers",
        headers=_bearer(admin),
        json=_broker_payload(),
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "broker_slug_taken"


async def test_create_broker_invalid_slug_returns_422(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    r = await client.post(
        "/api/v1/admin/brokers",
        headers=_bearer(admin),
        json=_broker_payload(slug="Futu_Caps"),
    )
    assert r.status_code == 422


# ─── 3. GET list ─────────────────────────────────────────────────


async def test_list_brokers_admin_returns_all_active(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    for slug in ("futubull", "tigerbrokers", "longbridge"):
        await client.post(
            "/api/v1/admin/brokers",
            headers=_bearer(admin),
            json=_broker_payload(slug=slug, name_zh=slug),
        )
    r = await client.get("/api/v1/admin/brokers", headers=_bearer(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    slugs = {item["slug"] for item in body["items"]}
    assert slugs == {"futubull", "tigerbrokers", "longbridge"}


async def test_list_brokers_excludes_soft_deleted_by_default(
    client: httpx.AsyncClient,
) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers", headers=_bearer(admin), json=_broker_payload()
    )
    await client.delete("/api/v1/admin/brokers/futubull", headers=_bearer(admin))

    r = await client.get("/api/v1/admin/brokers", headers=_bearer(admin))
    assert r.status_code == 200
    assert r.json()["total"] == 0

    r2 = await client.get(
        "/api/v1/admin/brokers?include_deleted=true", headers=_bearer(admin)
    )
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["is_deleted"] is True


async def test_list_brokers_includes_inactive_by_default(
    client: httpx.AsyncClient,
) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers",
        headers=_bearer(admin),
        json=_broker_payload(is_active=False),
    )
    r = await client.get("/api/v1/admin/brokers", headers=_bearer(admin))
    assert r.json()["total"] == 1

    r2 = await client.get(
        "/api/v1/admin/brokers?include_inactive=false", headers=_bearer(admin)
    )
    assert r2.json()["total"] == 0


# ─── 4. GET detail ───────────────────────────────────────────────


async def test_get_broker_detail_returns_partnership(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers", headers=_bearer(admin), json=_broker_payload()
    )
    r = await client.get("/api/v1/admin/brokers/futubull", headers=_bearer(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "futubull"
    assert body["partnership_type"] == "CPA"
    # Pydantic Decimal → JSON 用 str("150.0"); 不锁字符串形态 (兼容 "150.00" / "150.0")
    assert float(body["partnership_cpa_amount"]) == 150.0


async def test_get_broker_detail_not_found(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    r = await client.get(
        "/api/v1/admin/brokers/does-not-exist", headers=_bearer(admin)
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "broker_not_found"


async def test_get_broker_detail_can_see_soft_deleted(
    client: httpx.AsyncClient,
) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers", headers=_bearer(admin), json=_broker_payload()
    )
    await client.delete("/api/v1/admin/brokers/futubull", headers=_bearer(admin))

    r = await client.get("/api/v1/admin/brokers/futubull", headers=_bearer(admin))
    assert r.status_code == 200
    assert r.json()["is_deleted"] is True
    assert r.json()["deleted_at"] is not None


# ─── 5. PATCH ────────────────────────────────────────────────────


async def test_patch_broker_scalar_field(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers", headers=_bearer(admin), json=_broker_payload()
    )
    r = await client.patch(
        "/api/v1/admin/brokers/futubull",
        headers=_bearer(admin),
        json={
            "name_zh": "富途证券",
            "open_account_url": "https://new.example.com/open",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name_zh"] == "富途证券"
    assert body["open_account_url"] == "https://new.example.com/open"


async def test_patch_broker_jsonb_merge_preserves_other_keys(
    client: httpx.AsyncClient,
) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers", headers=_bearer(admin), json=_broker_payload()
    )
    r = await client.patch(
        "/api/v1/admin/brokers/futubull",
        headers=_bearer(admin),
        json={"promotion_patch": {"title": "改了标题"}},
    )
    assert r.status_code == 200
    promo = r.json()["promotion"]
    assert promo["title"] == "改了标题"
    assert promo["is_active"] is True
    assert promo["referral_url"] == "https://example.com/futu/legacy"


async def test_patch_broker_not_found(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    r = await client.patch(
        "/api/v1/admin/brokers/nonexistent",
        headers=_bearer(admin),
        json={"name_zh": "X"},
    )
    assert r.status_code == 404


async def test_patch_broker_empty_body_is_noop(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers", headers=_bearer(admin), json=_broker_payload()
    )
    r = await client.patch(
        "/api/v1/admin/brokers/futubull",
        headers=_bearer(admin),
        json={},
    )
    assert r.status_code == 200
    assert r.json()["name_zh"] == "富途牛牛"


# ─── 6. DELETE ───────────────────────────────────────────────────


async def test_soft_delete_broker(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers", headers=_bearer(admin), json=_broker_payload()
    )
    r = await client.delete("/api/v1/admin/brokers/futubull", headers=_bearer(admin))
    assert r.status_code == 204

    r2 = await client.get("/api/v1/brokers")
    assert r2.status_code == 200
    assert r2.json()["total"] == 0


async def test_delete_broker_idempotent(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers", headers=_bearer(admin), json=_broker_payload()
    )
    r1 = await client.delete("/api/v1/admin/brokers/futubull", headers=_bearer(admin))
    r2 = await client.delete("/api/v1/admin/brokers/futubull", headers=_bearer(admin))
    assert r1.status_code == 204
    assert r2.status_code == 204


async def test_delete_broker_not_found(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    r = await client.delete(
        "/api/v1/admin/brokers/never-existed", headers=_bearer(admin)
    )
    assert r.status_code == 404


# ─── 7. POST restore ─────────────────────────────────────────────


async def test_restore_soft_deleted_broker(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers", headers=_bearer(admin), json=_broker_payload()
    )
    await client.delete("/api/v1/admin/brokers/futubull", headers=_bearer(admin))

    r = await client.post(
        "/api/v1/admin/brokers/futubull/restore", headers=_bearer(admin)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["is_deleted"] is False
    assert body["is_active"] is True


async def test_restore_not_deleted_broker_is_noop(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers", headers=_bearer(admin), json=_broker_payload()
    )
    r = await client.post(
        "/api/v1/admin/brokers/futubull/restore", headers=_bearer(admin)
    )
    assert r.status_code == 200
    assert r.json()["is_deleted"] is False


# ─── 8. Data integrity: redirect 优先用顶层 open_account_url ──────


async def test_redirect_prefers_open_account_url_over_promotion(
    client: httpx.AsyncClient,
) -> None:
    """Sprint 11 BE-S11-A04: 顶层 open_account_url 优先级 > promotion.referral_url."""
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers",
        headers=_bearer(admin),
        json=_broker_payload(
            open_account_url="https://top-level.example.com/open",
            promotion={
                "is_active": True,
                "referral_url": "https://legacy.example.com/old",
            },
        ),
    )

    r = await client.get(
        "/api/v1/brokers/futubull/redirect",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "top-level.example.com" in r.headers["location"]
    assert "legacy.example.com" not in r.headers["location"]


async def test_redirect_falls_back_to_promotion_when_no_top_level(
    client: httpx.AsyncClient,
) -> None:
    """没填顶层 open_account_url 时, fallback 走 promotion.referral_url."""
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers",
        headers=_bearer(admin),
        json=_broker_payload(
            open_account_url=None,
            promotion={
                "is_active": True,
                "referral_url": "https://legacy.example.com/old",
            },
        ),
    )

    r = await client.get(
        "/api/v1/brokers/futubull/redirect",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "legacy.example.com" in r.headers["location"]


async def test_redirect_404_when_neither_url_set(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers",
        headers=_bearer(admin),
        json=_broker_payload(
            open_account_url=None,
            promotion={},
        ),
    )
    r = await client.get(
        "/api/v1/brokers/futubull/redirect",
        follow_redirects=False,
    )
    assert r.status_code == 404


# ─── 9. 公开路径不泄漏 partnership_* / 软删字段 ──────────────────────


async def test_public_list_does_not_expose_partnership(
    client: httpx.AsyncClient,
) -> None:
    admin, _ = await _admin_setup(client)
    await client.post(
        "/api/v1/admin/brokers", headers=_bearer(admin), json=_broker_payload()
    )
    r = await client.get("/api/v1/brokers")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert "partnership_type" not in item
    assert "partnership_cpa_amount" not in item
    assert "is_deleted" not in item
    assert "deleted_at" not in item
    assert item["open_account_url"] == "https://example.com/futu/open?ref=xgzh"


# ─── 10. DB 验证: alembic 0018 数据回填 ────────────────────────────


async def test_alembic_0018_backfills_open_account_url_from_promotion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """模拟 0018 之前的旧风格 broker (没顶层 URL), 然后跑回填 SQL 验证语义.

    schema_at_head fixture 已经把 brokers 表升到 head (含 open_account_url 列),
    所以这里只能验证 "INSERT 时 NULL → 用回填 SQL UPDATE 后变非 NULL", 实际
    migration 上线时只跑一次回填.
    """
    async with session_factory() as s:
        await s.execute(
            text(
                """
                INSERT INTO brokers (slug, name_zh, promotion, partnership_type)
                VALUES ('legacy', '旧风格券商',
                  '{"is_active": true, "referral_url": "https://legacy.com/r"}'::jsonb,
                  'NONE')
                """
            )
        )
        await s.commit()

        b = (
            await s.execute(select(Broker).where(Broker.slug == "legacy"))
        ).scalar_one()
        assert b.open_account_url is None, "INSERT 时没填顶层 URL"

        await s.execute(
            text(
                """
                UPDATE brokers
                SET open_account_url = promotion->>'referral_url'
                WHERE promotion ? 'referral_url'
                  AND promotion->>'referral_url' IS NOT NULL
                  AND open_account_url IS NULL
                """
            )
        )
        await s.commit()
        await s.refresh(b)
        assert b.open_account_url == "https://legacy.com/r"
