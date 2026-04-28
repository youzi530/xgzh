"""BE-S5-002 PIPL admin 审计接口集成测.

覆盖:
1.  缺 X-Admin-Token → 401
2.  错 X-Admin-Token → 401
3.  OPS_ADMIN_TOKEN 未设 → 503
4.  正确 token → 200, payload 结构齐全 (items / counts / sdks / consent / jurisdictions)
5.  counts.total_active_users 与 DB 实际行数对齐 (插数据 → 计数 +1)
6.  counts.total_users_lifetime 包含软删 / 禁用用户
7.  counts.total_feedbacks_with_ip 只计带 IP 的反馈
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models import Feedback, User

pytestmark = [pytest.mark.asyncio, pytest.mark.db]


ADMIN_TOKEN = "test-admin-token-pii-32-bytes-pad"


@pytest.fixture(autouse=True)
def _set_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_ADMIN_TOKEN", ADMIN_TOKEN)
    get_settings.cache_clear()


# ─── 1. 鉴权 ───────────────────────────────────────────────────────


async def test_no_token_returns_401(client: httpx.AsyncClient) -> None:
    res = await client.get("/api/v1/admin/pii-inventory")
    assert res.status_code == 401
    body = res.json()
    assert body["detail"]["code"] == "admin_token_invalid"


async def test_wrong_token_returns_401(client: httpx.AsyncClient) -> None:
    res = await client.get(
        "/api/v1/admin/pii-inventory",
        headers={"X-Admin-Token": "definitely-not-the-token"},
    )
    assert res.status_code == 401


async def test_admin_disabled_when_token_unset(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPS_ADMIN_TOKEN", "")
    get_settings.cache_clear()
    res = await client.get(
        "/api/v1/admin/pii-inventory",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert res.status_code == 503
    body = res.json()
    assert body["detail"]["code"] == "admin_disabled"


# ─── 2. 正常路径 ────────────────────────────────────────────────────


async def test_get_pii_inventory_returns_full_payload(
    client: httpx.AsyncClient,
) -> None:
    res = await client.get(
        "/api/v1/admin/pii-inventory",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # 顶层结构
    for key in (
        "items",
        "data_export_jurisdictions",
        "consent_mechanism",
        "third_party_sdks",
        "counts",
        "spec_version",
    ):
        assert key in body, f"响应缺顶层字段 {key!r}"

    # items: 至少 12 条 PII
    assert len(body["items"]) >= 12

    # 每条 PII item 字段齐全
    for item in body["items"]:
        for key in (
            "field",
            "table",
            "scenario",
            "purpose",
            "legal_basis",
            "retention_days_after_logout",
            "is_sensitive",
        ):
            assert key in item, f"PII item 缺字段 {key!r}: {item}"

    # 出境清单空 (MVP)
    assert body["data_export_jurisdictions"] == []

    # 同意机制是 explicit_opt_in
    assert body["consent_mechanism"]["type"] == "explicit_opt_in"

    # 第三方 SDK 列表非空
    assert len(body["third_party_sdks"]) >= 3
    for sdk in body["third_party_sdks"]:
        assert sdk["url"].startswith("https://")

    # 计数全部是 int
    counts = body["counts"]
    for key in (
        "total_active_users",
        "total_users_lifetime",
        "total_push_tokens",
        "total_feedbacks_with_ip",
        "total_auth_sessions",
    ):
        assert isinstance(counts[key], int), f"counts.{key} 必须是 int"


# ─── 3. counts 真实反映 DB 行数 ─────────────────────────────────────


async def test_counts_reflect_db_state(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """先空库拉一次 (counts 应全 0), 再插 2 active + 1 disabled + 1 softdeleted, 再拉一次确认增量."""
    res0 = await client.get(
        "/api/v1/admin/pii-inventory",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    counts0 = res0.json()["counts"]
    assert counts0["total_active_users"] == 0
    assert counts0["total_users_lifetime"] == 0

    async with session_factory() as session:
        # 2 个 active
        for i in range(2):
            session.add(
                User(
                    phone=f"+8613000{i:07d}",
                    invite_code=f"PII{i:05d}A",
                    status=1,
                )
            )
        # 1 个 disabled (status=0)
        session.add(
            User(
                phone="+8613100000000",
                invite_code="PIIDIS",
                status=0,
            )
        )
        # 1 个 softdeleted (status=1, deleted_at != null)
        session.add(
            User(
                phone="+8613200000000",
                invite_code="PIISD0",
                status=1,
                deleted_at=datetime.now(UTC),
            )
        )
        await session.commit()

    res1 = await client.get(
        "/api/v1/admin/pii-inventory",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    counts1 = res1.json()["counts"]
    assert counts1["total_active_users"] == 2, (
        f"active 应只算 status=1 + 未软删 = 2, 实际 {counts1['total_active_users']}"
    )
    assert counts1["total_users_lifetime"] == 4, (
        "历史累计应含 disabled + softdeleted, 实际 "
        f"{counts1['total_users_lifetime']}"
    )


async def test_counts_feedback_with_ip_only(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``total_feedbacks_with_ip`` 仅计 ip_inet 非空的反馈."""
    async with session_factory() as session:
        # 一条无 IP (走 admin 内部 / 测试)
        session.add(
            Feedback(
                feedback_id=uuid.uuid4(),
                category="bug",
                content="no ip",
                platform="h5",
                ip_inet=None,
            )
        )
        # 两条有 IP
        for i in range(2):
            session.add(
                Feedback(
                    feedback_id=uuid.uuid4(),
                    category="other",
                    content=f"with ip {i}",
                    platform="h5",
                    ip_inet=f"203.0.113.{i + 1}",
                )
            )
        await session.commit()

    res = await client.get(
        "/api/v1/admin/pii-inventory",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    counts = res.json()["counts"]
    assert counts["total_feedbacks_with_ip"] == 2, (
        f"应只计 ip 非空的 2 条, 实际 {counts['total_feedbacks_with_ip']}"
    )
