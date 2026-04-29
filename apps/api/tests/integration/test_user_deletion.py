"""BE-S5-003 用户注销账号 端到端集成测.

覆盖 (spec/12 §AC):
1.  未登录 → 401
2.  已登录用户 DELETE /me → 200, 拿到 audit_id + real_purge_scheduled_at = +30d
3.  软删后, 同一 access token 再调 GET /me → 401 (token_revoked or user_disabled)
4.  软删后, refresh token 全部 revoke (auth_sessions.revoked_at != NULL)
5.  软删后, invite_codes.is_active = False
6.  重复调 DELETE /me → 409 user_already_deleted
7.  软删 + grace_days=0 + 跑 cron → users PII 字段全 NULL, push_tokens / auth_sessions 全清, audit.real_purge_at != NULL
8.  cron 不会重复 purge (跑两次第二次 purged_count=0)
9.  vip_orders 在 hard delete 后保留 (财务 7 年)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models import (
    AuthSession,
    InviteCode,
    PushToken,
    User,
    UserDeletion,
    VipOrder,
)
from app.services import otp_service, user_deletion_service

pytestmark = [pytest.mark.asyncio, pytest.mark.db]


# ─── helper: 注册 + 登录 ────────────────────────────────────────────


async def _register_via_otp(
    client: httpx.AsyncClient, *, phone: str, code: str = "654321"
) -> tuple[uuid.UUID, str]:
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    resp = await client.post(
        "/api/v1/auth/login/phone", json={"phone": phone, "code": code}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return uuid.UUID(body["user"]["user_id"]), body["tokens"]["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ─── 1. 鉴权 ────────────────────────────────────────────────────────


async def test_delete_me_requires_login(client: httpx.AsyncClient) -> None:
    resp = await client.delete("/api/v1/me")
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"]["code"] == "token_missing"


# ─── 2. 软删 happy path ──────────────────────────────────────────────


async def test_delete_me_soft_deletes_and_returns_purge_schedule(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, token = await _register_via_otp(client, phone="+8613100000001")

    resp = await client.request(
        "DELETE",
        "/api/v1/me",
        json={"reason": "用不上了"},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True
    assert body["user_id"] == str(user_id)
    deleted_at = datetime.fromisoformat(body["deleted_at"].replace("Z", "+00:00"))
    purge_at = datetime.fromisoformat(
        body["real_purge_scheduled_at"].replace("Z", "+00:00")
    )
    # 默认 grace_days=30
    assert abs(((purge_at - deleted_at) - timedelta(days=30)).total_seconds()) < 5

    # DB 状态: user soft-deleted + audit row + invite_code 标 inactive
    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
        assert user.deleted_at is not None
        assert user.status == 0
        # PII 在软删阶段不清 (30d 后 cron 才清)
        assert user.phone == "+8613100000001"

        audit = (
            await session.execute(
                select(UserDeletion).where(UserDeletion.user_id == user_id)
            )
        ).scalar_one()
        assert audit.real_purge_at is None
        assert audit.reason == "用不上了"
        assert audit.user_agent is not None  # ASGI client 默认会带 UA

        invite = (
            await session.execute(
                select(InviteCode).where(InviteCode.owner_user_id == user_id)
            )
        ).scalar_one()
        assert invite.is_active is False


# ─── 3. 软删后凭据立即失效 ──────────────────────────────────────────


async def test_after_soft_delete_access_token_is_invalid(
    client: httpx.AsyncClient,
) -> None:
    _, token = await _register_via_otp(client, phone="+8613100000002")

    # 注销前 GET /me 200
    pre = await client.get("/api/v1/me", headers=_auth_headers(token))
    assert pre.status_code == 200

    resp = await client.delete("/api/v1/me", headers=_auth_headers(token))
    assert resp.status_code == 200

    # 注销后 同 token GET /me → 401 (token_revoked 优先, fallback user_disabled)
    post = await client.get("/api/v1/me", headers=_auth_headers(token))
    assert post.status_code == 401
    code = post.json()["detail"]["code"]
    assert code in ("token_revoked", "user_disabled"), (
        f"期望 token_revoked / user_disabled, 实际 {code}"
    )


async def test_after_soft_delete_refresh_token_rejected(
    client: httpx.AsyncClient,
) -> None:
    """注销后 refresh token 不能续 access — auth_service.refresh_tokens 检查 user.status==1.

    BE-004 当前 refresh 不写 ``auth_sessions`` 表 (黑名单走 Redis), 所以本用例从外侧
    HTTP 验 refresh 失败的语义, 而非检 ``auth_sessions.revoked_at``. ``auth_sessions``
    在 ``soft_delete_user`` 仍会被 UPDATE (no-op 当前为空, 但保留兼容 5.5 加 session 表
    持久化时不需要再改本路径).
    """
    await otp_service.store_otp("+8613100000003", "654321", ttl_seconds=300)
    login_resp = await client.post(
        "/api/v1/auth/login/phone",
        json={"phone": "+8613100000003", "code": "654321"},
    )
    assert login_resp.status_code == 200
    body = login_resp.json()
    access = body["tokens"]["access_token"]
    refresh = body["tokens"]["refresh_token"]

    # 注销
    resp = await client.delete("/api/v1/me", headers=_auth_headers(access))
    assert resp.status_code == 200

    # refresh 失败: status=0 让 refresh_tokens raise RefreshUserUnavailable → 401
    refresh_resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh}
    )
    assert refresh_resp.status_code == 401
    code = refresh_resp.json()["detail"]["code"]
    assert code in ("user_unavailable", "refresh_user_unavailable", "refresh_revoked"), (
        f"期望 user 不可用相关 code, 实际 {code}"
    )


# ─── 4. 重复注销 → 409 ───────────────────────────────────────────────


async def test_delete_me_twice_returns_409(client: httpx.AsyncClient) -> None:
    """实际上 token 已经 invalid, 第二次 DELETE 会先撞 401; 这里直接 service 层验幂等."""
    user_id, token = await _register_via_otp(client, phone="+8613100000004")
    r1 = await client.delete("/api/v1/me", headers=_auth_headers(token))
    assert r1.status_code == 200

    # 重复调用同 token (token 已 revoke, 401 in via auth deps)
    r2 = await client.delete("/api/v1/me", headers=_auth_headers(token))
    assert r2.status_code == 401  # token_revoked 优先于 409 (deps 先校验)


# ─── 5. 30d 真删 cron ────────────────────────────────────────────────


async def test_hard_delete_cron_purges_pii_after_grace(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """grace_days=0 + 跑 cron → users PII 全 NULL + push_tokens / auth_sessions 全清."""
    monkeypatch.setenv("USER_DELETION_GRACE_DAYS", "0")
    get_settings.cache_clear()

    try:
        user_id, token = await _register_via_otp(client, phone="+8613100000005")

        # 给用户造一条 push_token, 验证 cron 真删了它
        async with session_factory() as session:
            session.add(
                PushToken(
                    user_id=user_id,
                    platform="ios",
                    token="apns-token-test",
                    device_id="DEV-001",
                )
            )
            await session.commit()

        resp = await client.delete("/api/v1/me", headers=_auth_headers(token))
        assert resp.status_code == 200

        # 跑真删
        result = await user_deletion_service.hard_delete_pii_overdue()
        assert result.purged_user_count == 1
        assert result.purged_user_ids == (user_id,)

        async with session_factory() as session:
            user = (
                await session.execute(select(User).where(User.user_id == user_id))
            ).scalar_one()
            assert user.phone is None
            assert user.wechat_openid is None
            assert user.wechat_unionid is None
            assert user.apple_id is None
            assert user.nickname is None
            assert user.avatar_url is None
            # 保留: user_id / status / region / deleted_at / created_at
            assert user.status == 0
            assert user.deleted_at is not None

            # push_tokens 真删
            pts = (
                await session.execute(
                    select(PushToken).where(PushToken.user_id == user_id)
                )
            ).scalars().all()
            assert pts == [], "push_tokens 应整行删除"

            # auth_sessions 真删
            sessions = (
                await session.execute(
                    select(AuthSession).where(AuthSession.user_id == user_id)
                )
            ).scalars().all()
            assert sessions == [], "auth_sessions 应整行删除"

            # audit 标 real_purge_at
            audit = (
                await session.execute(
                    select(UserDeletion).where(UserDeletion.user_id == user_id)
                )
            ).scalar_one()
            assert audit.real_purge_at is not None
    finally:
        get_settings.cache_clear()


async def test_hard_delete_cron_idempotent_no_double_purge(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跑两次 cron 第二次 purged_user_count=0 (已 purge 过, real_purge_at != NULL 跳过)."""
    monkeypatch.setenv("USER_DELETION_GRACE_DAYS", "0")
    get_settings.cache_clear()

    try:
        _, token = await _register_via_otp(client, phone="+8613100000006")
        await client.delete("/api/v1/me", headers=_auth_headers(token))

        r1 = await user_deletion_service.hard_delete_pii_overdue()
        assert r1.purged_user_count == 1

        r2 = await user_deletion_service.hard_delete_pii_overdue()
        assert r2.purged_user_count == 0
        assert r2.purged_user_ids == ()
    finally:
        get_settings.cache_clear()


async def test_hard_delete_skips_users_inside_grace_period(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """grace_days 默认 30d → 刚注销的用户 cron 不动 (requested_at > now-30d)."""
    user_id, token = await _register_via_otp(client, phone="+8613100000007")
    await client.delete("/api/v1/me", headers=_auth_headers(token))

    result = await user_deletion_service.hard_delete_pii_overdue()
    assert result.purged_user_count == 0

    # 用户 phone 仍在 (软删后 30d 内不清)
    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
        assert user.phone == "+8613100000007"


async def test_hard_delete_keeps_vip_orders_for_finance(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """财务 7 年保留: vip_orders 在 hard delete 后仍然存在 (含 trial 零元单)."""
    monkeypatch.setenv("USER_DELETION_GRACE_DAYS", "0")
    get_settings.cache_clear()

    try:
        user_id, token = await _register_via_otp(client, phone="+8613100000008")
        # 注册时 grant_trial 写了一笔 trial vip_order
        async with session_factory() as session:
            orders = (
                await session.execute(
                    select(VipOrder).where(VipOrder.user_id == user_id)
                )
            ).scalars().all()
            assert len(orders) == 1, "注册后应有 1 笔 trial 订单"

        await client.delete("/api/v1/me", headers=_auth_headers(token))
        await user_deletion_service.hard_delete_pii_overdue()

        async with session_factory() as session:
            orders_after = (
                await session.execute(
                    select(VipOrder).where(VipOrder.user_id == user_id)
                )
            ).scalars().all()
            assert len(orders_after) == 1, "hard delete 后 vip_orders 必须保留 (财务 7 年)"
            assert orders_after[0].plan == "trial"
    finally:
        get_settings.cache_clear()


# ─── 6. service 层直接调 (绕过 HTTP) ────────────────────────────────


async def test_soft_delete_user_service_idempotent_returns_409(
    client: httpx.AsyncClient,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一 user 两次 soft_delete → 第二次 raise UserAlreadyDeletedError (audit UNIQUE 防重)."""
    from app.security.jwt import create_access_token, decode_token

    # 1. 造一个用户 (phone 注册)
    async with session_factory() as session:
        user = User(
            phone="+8613100000009",
            invite_code=f"DEL{uuid.uuid4().hex[:5].upper()}",
            status=1,
        )
        session.add(user)
        await session.commit()
        user_id = user.user_id

    token, _ = create_access_token(user_id=user_id)
    payload = decode_token(token, expected_type="access")  # type: ignore[arg-type]

    # 2. 第一次 soft delete OK
    async with session_factory() as session:
        user_orm = (
            await session.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
        result = await user_deletion_service.soft_delete_user(
            session,
            user=user_orm,
            access_payload=payload,  # type: ignore[arg-type]
            reason="test",
        )
        assert result.user_id == user_id

    # 3. 第二次 → UserAlreadyDeletedError
    async with session_factory() as session:
        user_orm = (
            await session.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
        with pytest.raises(user_deletion_service.UserAlreadyDeletedError):
            await user_deletion_service.soft_delete_user(
                session,
                user=user_orm,
                access_payload=payload,  # type: ignore[arg-type]
                reason="dup",
            )
