"""BE-S5-006 admin 数据看板集成测.

覆盖:
1.  鉴权: 缺 token / 错 token / 未配置 token
2.  JSON 空库: 全 0 + schema 完整
3.  JSON 真实数据: 插 user / chat_session / chat_message / token_usage / vip_membership
    → 各指标数字与插入数据对齐
4.  窗口剔除: 老于 ``days`` 的数据不应被算入
5.  HTML 格式: ``?format=html`` 返 ``text/html`` + 关键文本
6.  参数边界: ``?days=0`` / ``?days=999`` / ``?format=xml`` → 422
7.  VIP 转化分母处理: 全 trialing 时 trial_to_paid_pct = 0
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models import (
    ChatMessage,
    ChatSession,
    ChatTokenUsage,
    User,
    VipMembership,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.db]

ADMIN_TOKEN = "test-admin-token-dashboard-32-byte"


@pytest.fixture(autouse=True)
def _set_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_ADMIN_TOKEN", ADMIN_TOKEN)
    get_settings.cache_clear()


# ─── helpers ──────────────────────────────────────────────────────


async def _make_user(
    session: AsyncSession,
    *,
    phone: str,
    invite_code: str,
    last_active_offset: timedelta = timedelta(0),
    status: int = 1,
) -> uuid.UUID:
    """造一个 user, last_active_at = now() - last_active_offset.

    ⚠️ ``users.last_active_at`` 是 ``TIMESTAMP WITHOUT TIME ZONE`` (naive),
    必须传 naive datetime 否则 asyncpg 拒绝.
    """
    user = User(
        phone=phone,
        invite_code=invite_code,
        status=status,
    )
    session.add(user)
    await session.flush()
    if last_active_offset.total_seconds() > 0:
        # naive UTC = aware now() 减去 tzinfo
        user.last_active_at = (
            datetime.now(UTC) - last_active_offset
        ).replace(tzinfo=None)
    return user.user_id


async def _make_chat_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    created_offset: timedelta = timedelta(0),
) -> uuid.UUID:
    s = ChatSession(
        user_id=user_id,
        title="test-session",
    )
    session.add(s)
    await session.flush()
    if created_offset.total_seconds() > 0:
        s.created_at = datetime.now(UTC) - created_offset
    return s.session_id


async def _make_message(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    role: str,
    created_offset: timedelta = timedelta(0),
) -> uuid.UUID:
    m = ChatMessage(
        session_id=session_id,
        role=role,
        content=f"hi {role}",
    )
    session.add(m)
    await session.flush()
    if created_offset.total_seconds() > 0:
        m.created_at = datetime.now(UTC) - created_offset
    return m.message_id


async def _make_token_usage(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
    input_tokens: int = 100,
    output_tokens: int = 200,
    cost_cny: str = "0.012",
    created_offset: timedelta = timedelta(0),
) -> None:
    u = ChatTokenUsage(
        message_id=message_id,
        model="zhipu/glm-4-flash",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_cny=Decimal(cost_cny),
        provider="zhipu",
    )
    session.add(u)
    await session.flush()
    if created_offset.total_seconds() > 0:
        u.created_at = datetime.now(UTC) - created_offset


# ─── 1. 鉴权 ───────────────────────────────────────────────────────


async def test_no_token_returns_401(client: httpx.AsyncClient) -> None:
    res = await client.get("/api/v1/admin/dashboard")
    assert res.status_code == 401


async def test_wrong_token_returns_401(client: httpx.AsyncClient) -> None:
    res = await client.get(
        "/api/v1/admin/dashboard",
        headers={"X-Admin-Token": "nope"},
    )
    assert res.status_code == 401


async def test_admin_disabled_when_token_unset(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPS_ADMIN_TOKEN", "")
    get_settings.cache_clear()
    res = await client.get(
        "/api/v1/admin/dashboard",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert res.status_code == 503


# ─── 2. 空库 JSON 路径 ──────────────────────────────────────────────


async def test_empty_db_json_payload_structure(client: httpx.AsyncClient) -> None:
    """空库 → 全 0 + 顶层字段齐全 + 6 大区段都在."""
    res = await client.get(
        "/api/v1/admin/dashboard",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["window_days"] == 1
    for section in (
        "user_activity",
        "registration",
        "vip_conversion",
        "agent_usage",
        "error_rate",
        "llm_performance",
    ):
        assert section in body, f"缺顶层段 {section!r}"

    # 空库下所有计数字段应为 0
    assert body["user_activity"]["distinct_active_users"] == 0
    assert body["registration"]["new_users_in_window"] == 0
    assert body["registration"]["total_users_lifetime"] == 0
    assert body["vip_conversion"]["total_memberships"] == 0
    assert body["vip_conversion"]["trial_to_paid_pct"] == 0.0
    assert body["agent_usage"]["sessions_in_window"] == 0
    assert body["agent_usage"]["llm_calls_in_window"] == 0
    assert body["agent_usage"]["total_cost_cny"] == 0.0
    assert body["llm_performance"]["avg_input_tokens_per_call"] == 0.0


# ─── 3. 数据真实反映 DB ─────────────────────────────────────────────


async def test_dashboard_reflects_real_data(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """造 3 个 user (2 active 1 disabled), 2 chat_session (1 with msgs+usage), 3 vip 状态."""
    async with session_factory() as session:
        # 3 个 user, 2 active + 1 disabled, 全在窗口内 (last_active = now)
        u1 = await _make_user(session, phone="+8613100000001", invite_code="DSH001")
        u2 = await _make_user(session, phone="+8613100000002", invite_code="DSH002")
        u3 = await _make_user(  # noqa: F841 - 用于 lifetime 计数
            session,
            phone="+8613100000003",
            invite_code="DSH003",
            status=0,  # disabled, 不计入 DAU
        )

        # 2 chat_session, 都在窗口内
        s1 = await _make_chat_session(session, user_id=u1)
        s2 = await _make_chat_session(session, user_id=u2)  # noqa: F841 - 计数用

        # session 1 下 2 条 user message + 1 条 assistant
        m1 = await _make_message(session, session_id=s1, role="user")
        await _make_message(session, session_id=s1, role="user")
        ma = await _make_message(session, session_id=s1, role="assistant")

        # 2 行 token_usage 关联到 m1 / ma
        await _make_token_usage(
            session,
            message_id=m1,
            input_tokens=100,
            output_tokens=200,
            cost_cny="0.0100",
        )
        await _make_token_usage(
            session,
            message_id=ma,
            input_tokens=300,
            output_tokens=400,
            cost_cny="0.0300",
        )

        # VIP: 1 trialing, 1 active, 1 expired
        now = datetime.now(UTC)
        session.add(
            VipMembership(
                user_id=u1,
                status="trialing",
                plan="trial",
                start_at=now - timedelta(days=1),
                end_at=now + timedelta(days=6),
            )
        )
        session.add(
            VipMembership(
                user_id=u2,
                status="active",
                plan="monthly",
                start_at=now - timedelta(days=10),
                end_at=now + timedelta(days=20),
            )
        )
        # 第 3 条 VIP 给一个新建 user (status=1) — 否则 unique(user_id) 冲突
        u_exp = await _make_user(
            session, phone="+8613100000004", invite_code="DSH004"
        )
        session.add(
            VipMembership(
                user_id=u_exp,
                status="expired",
                plan="trial",
                start_at=now - timedelta(days=14),
                end_at=now - timedelta(days=7),
            )
        )

        await session.commit()

    res = await client.get(
        "/api/v1/admin/dashboard?days=1",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # DAU: u1 / u2 / u_exp 都 status=1, u3 status=0 不计 → 3
    assert body["user_activity"]["distinct_active_users"] == 3, (
        f"DAU 应为 3 (status=1 用户), 实际 {body['user_activity']}"
    )
    # 注册: 4 个全部新建在窗口内
    assert body["registration"]["new_users_in_window"] == 4
    assert body["registration"]["total_users_lifetime"] == 4

    # VIP: 3 行, 转化率分母 = active(1) + expired(1) = 2, 分子 active=1 → 50%
    vip = body["vip_conversion"]
    assert vip["total_memberships"] == 3
    assert vip["trial_memberships"] == 1
    assert vip["active_paid_memberships"] == 1
    assert vip["expired_memberships"] == 1
    assert vip["trial_to_paid_pct"] == 50.0

    # Agent: 2 sessions, 2 user msgs, 2 llm calls
    agent = body["agent_usage"]
    assert agent["sessions_in_window"] == 2
    assert agent["user_messages_in_window"] == 2
    assert agent["llm_calls_in_window"] == 2
    assert agent["total_input_tokens"] == 400
    assert agent["total_output_tokens"] == 600
    assert agent["total_cost_cny"] == pytest.approx(0.04, rel=1e-3)

    # LLM 性能: avg = total / 2
    perf = body["llm_performance"]
    assert perf["avg_input_tokens_per_call"] == 200.0
    assert perf["avg_output_tokens_per_call"] == 300.0
    assert perf["avg_cost_cny_per_call"] == pytest.approx(0.02, rel=1e-3)


# ─── 4. 窗口剔除老数据 ──────────────────────────────────────────────


async def test_window_excludes_old_data(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``days=1`` 应剔除 2 天前的 chat_session / token_usage / 注册 / DAU."""
    async with session_factory() as session:
        # 老 user: created_at 不能直接改 (服务器默认), 但 last_active_at 可控
        u_old = await _make_user(
            session,
            phone="+8613299999999",
            invite_code="OLD001",
            last_active_offset=timedelta(days=2),  # 2 天前活跃
        )
        # 老 chat_session: 2 天前
        s_old = await _make_chat_session(
            session,
            user_id=u_old,
            created_offset=timedelta(days=2),
        )
        # 老 token_usage: 2 天前
        m_old = await _make_message(
            session,
            session_id=s_old,
            role="assistant",
            created_offset=timedelta(days=2),
        )
        await _make_token_usage(
            session,
            message_id=m_old,
            created_offset=timedelta(days=2),
        )
        await session.commit()

    # days=1 视图: 老用户活跃应被剔除, 但 lifetime 计数应包含
    res = await client.get(
        "/api/v1/admin/dashboard?days=1",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    body = res.json()
    assert body["user_activity"]["distinct_active_users"] == 0, (
        "老用户 last_active_at 在窗口外, DAU 应不计入"
    )
    assert body["registration"]["total_users_lifetime"] == 1, (
        "lifetime 应含老用户"
    )
    assert body["agent_usage"]["sessions_in_window"] == 0
    assert body["agent_usage"]["llm_calls_in_window"] == 0


# ─── 5. HTML 格式 ───────────────────────────────────────────────────


async def test_html_format_returns_text_html(client: httpx.AsyncClient) -> None:
    res = await client.get(
        "/api/v1/admin/dashboard?format=html",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    text = res.text
    # 关键文案 / 表格段都在
    assert "XGZH Admin Dashboard" in text
    assert "用户活跃" in text
    assert "VIP 转化" in text
    assert "Agent 调用" in text
    assert "<table>" in text
    # 链接到 JSON 视图
    assert "format=json" in text


# ─── 6. 参数边界 ────────────────────────────────────────────────────


async def test_days_zero_is_rejected(client: httpx.AsyncClient) -> None:
    res = await client.get(
        "/api/v1/admin/dashboard?days=0",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert res.status_code == 422


async def test_days_too_large_is_rejected(client: httpx.AsyncClient) -> None:
    res = await client.get(
        "/api/v1/admin/dashboard?days=999",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert res.status_code == 422


async def test_unknown_format_is_rejected(client: httpx.AsyncClient) -> None:
    res = await client.get(
        "/api/v1/admin/dashboard?format=xml",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert res.status_code == 422


# ─── 7. trial→paid 转化率分母为 0 ──────────────────────────────────


async def test_trial_to_paid_pct_zero_when_no_decision_made(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """全部 trialing, 无 active / expired → 转化率 = 0 (分母 = 0 守护)."""
    async with session_factory() as session:
        u = await _make_user(
            session, phone="+8613177777777", invite_code="TRL001"
        )
        now = datetime.now(UTC)
        session.add(
            VipMembership(
                user_id=u,
                status="trialing",
                plan="trial",
                start_at=now - timedelta(days=1),
                end_at=now + timedelta(days=6),
            )
        )
        await session.commit()

    res = await client.get(
        "/api/v1/admin/dashboard",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    body = res.json()
    assert body["vip_conversion"]["trial_memberships"] == 1
    assert body["vip_conversion"]["active_paid_memberships"] == 0
    assert body["vip_conversion"]["expired_memberships"] == 0
    assert body["vip_conversion"]["trial_to_paid_pct"] == 0.0


# ─── 8. days 参数透传到响应 ─────────────────────────────────────────


async def test_window_days_echoed_in_response(client: httpx.AsyncClient) -> None:
    res = await client.get(
        "/api/v1/admin/dashboard?days=7",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    body = res.json()
    assert body["window_days"] == 7

    res_html = await client.get(
        "/api/v1/admin/dashboard?days=30&format=html",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert "过去 <b>30</b> 天" in res_html.text
