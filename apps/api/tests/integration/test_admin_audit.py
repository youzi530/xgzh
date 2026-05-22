"""Sprint 11 BE-S11-E03: admin_audit_service 端到端测试.

覆盖:
- admin 写操作后 audit log 落入 DB
- log_admin_action 失败 (e.g. 表不存在) 不影响业务返回
- diff_dict 工具函数
- list_audit_logs 分页 + filter (admin_user_id / target_type / action)
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import AdminAuditLog
from app.services import otp_service
from app.services.admin_audit_service import diff_dict, list_audit_logs

pytestmark = pytest.mark.db


# ─── helpers ────────────────────────────────────────────────


async def _admin_token(client: httpx.AsyncClient) -> str:
    full_phone = "+8613007458553"
    await otp_service.store_otp(full_phone, "111111", ttl_seconds=300)
    r = await client.post(
        "/api/v1/auth/login/phone",
        json={"phone": full_phone, "code": "111111"},
    )
    return r.json()["tokens"]["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ─── 1. 业务写操作 → audit log 落库 ────────────────────────────


async def test_create_broker_records_audit(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    """新建 broker 后, admin_audit_logs 表有一条 (action=create, target=broker)."""
    admin = await _admin_token(client)
    r = await client.post(
        "/api/v1/admin/brokers",
        headers=_h(admin),
        json={
            "slug": "audit-test-broker",
            "name_zh": "审计测试券商",
            "market_support": ["HK"],
        },
    )
    assert r.status_code == 201

    async with session_factory() as s:
        rows = (
            (
                await s.execute(
                    select(AdminAuditLog).where(
                        AdminAuditLog.target_type == "broker",
                        AdminAuditLog.target_id == "audit-test-broker",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].action == "create"
    assert rows[0].result == "success"
    assert rows[0].admin_user_id is not None


async def test_create_knowledge_records_audit(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    admin = await _admin_token(client)
    r = await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json={
            "slug": "audit-test-article",
            "title": "审计测试文章",
            "category": "general",
            "content_md": "# audit\n\n测试",
        },
    )
    assert r.status_code == 201

    async with session_factory() as s:
        rows = (
            (
                await s.execute(
                    select(AdminAuditLog).where(
                        AdminAuditLog.target_type == "knowledge_article",
                        AdminAuditLog.action == "create",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].result == "success"


async def test_update_records_diff(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    """PATCH 后 audit log 的 changes_json 含被改字段."""
    admin = await _admin_token(client)
    # 先建一个 broker
    await client.post(
        "/api/v1/admin/brokers",
        headers=_h(admin),
        json={
            "slug": "audit-diff-broker",
            "name_zh": "Diff Broker",
            "market_support": ["HK"],
        },
    )
    # PATCH 改 name
    r = await client.patch(
        "/api/v1/admin/brokers/audit-diff-broker",
        headers=_h(admin),
        json={"name_zh": "新名字"},
    )
    assert r.status_code == 200

    async with session_factory() as s:
        rows = (
            (
                await s.execute(
                    select(AdminAuditLog).where(
                        AdminAuditLog.target_id == "audit-diff-broker",
                        AdminAuditLog.action == "update",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].changes_json is not None
    assert "name_zh" in rows[0].changes_json


async def test_failure_not_logged_in_db(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    """slug 重复 → 409, audit 表只有第一条 create 的 success log; failure 不入库."""
    admin = await _admin_token(client)
    # 先建一次
    await client.post(
        "/api/v1/admin/brokers",
        headers=_h(admin),
        json={
            "slug": "audit-fail-broker",
            "name_zh": "Fail Broker",
            "market_support": ["HK"],
        },
    )
    # 再建一次 → 409
    r = await client.post(
        "/api/v1/admin/brokers",
        headers=_h(admin),
        json={
            "slug": "audit-fail-broker",
            "name_zh": "Fail Broker",
            "market_support": ["HK"],
        },
    )
    assert r.status_code == 409

    async with session_factory() as s:
        rows = (
            (
                await s.execute(
                    select(AdminAuditLog).where(
                        AdminAuditLog.target_id == "audit-fail-broker",
                    )
                )
            )
            .scalars()
            .all()
        )
    # 只有 1 条 success, 没有 failure (failure 已 logger.warning 但不落 audit)
    assert len(rows) == 1
    assert rows[0].result == "success"


# ─── 2. diff_dict 工具 ──────────────────────────────────────


async def test_diff_dict_basic() -> None:
    """diff 只返不同的 key."""
    before = {"a": 1, "b": 2, "c": 3}
    after = {"a": 1, "b": 9, "d": 4}
    d = diff_dict(before, after)
    assert "a" not in d  # 相等不返
    assert d["b"] == [2, 9]
    assert d["c"] == [3, None]  # 删除字段
    assert d["d"] == [None, 4]  # 新增字段


async def test_diff_dict_with_keys_filter() -> None:
    """限定 keys 时只看这几个."""
    before = {"a": 1, "b": 2, "c": 3}
    after = {"a": 9, "b": 8, "c": 7}
    d = diff_dict(before, after, keys=["a", "c"])
    assert "a" in d
    assert "c" in d
    assert "b" not in d


# ─── 3. list_audit_logs ────────────────────────────────────


async def test_list_audit_logs_filter(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    """list_audit_logs 按 target_type filter 工作."""
    admin = await _admin_token(client)
    await client.post(
        "/api/v1/admin/brokers",
        headers=_h(admin),
        json={"slug": "list-audit-b", "name_zh": "L", "market_support": ["HK"]},
    )
    await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json={
            "slug": "list-audit-k",
            "title": "K",
            "category": "general",
            "content_md": "# x",
        },
    )

    async with session_factory() as s:
        rows_broker, total_b = await list_audit_logs(s, target_type="broker")
        rows_kn, total_k = await list_audit_logs(s, target_type="knowledge_article")

    assert any(r.target_id == "list-audit-b" for r in rows_broker)
    assert any(r.action == "create" for r in rows_kn)
    assert total_b >= 1
    assert total_k >= 1


async def test_list_audit_logs_admin_filter(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    """按 admin_user_id filter 只返该 admin 的操作."""
    admin = await _admin_token(client)
    r = await client.post(
        "/api/v1/admin/brokers",
        headers=_h(admin),
        json={
            "slug": "audit-admin-filter",
            "name_zh": "A",
            "market_support": ["HK"],
        },
    )
    assert r.status_code == 201

    # 从 JWT 拿 admin_user_id (login response 返了 user_id)
    full_phone = "+8613007458553"
    await otp_service.store_otp(full_phone, "111111", ttl_seconds=300)
    r2 = await client.post(
        "/api/v1/auth/login/phone",
        json={"phone": full_phone, "code": "111111"},
    )
    admin_user_id = uuid.UUID(r2.json()["user"]["user_id"])

    async with session_factory() as s:
        rows, total = await list_audit_logs(s, admin_user_id=admin_user_id)
    assert total >= 1
    assert all(r.admin_user_id == admin_user_id for r in rows)
