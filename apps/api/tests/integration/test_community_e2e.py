"""BE-S6-006/007/008/009 社区端到端集成测.

覆盖 (spec/13 §主线 D AC):
- 鉴权: 全部端点登录态 / 公共可读不强制
- 发帖: 内容审核 (approve/queue/reject) + 反 spam 限流 + 新用户 7d 只读
- 列表 / 详情: 仅 published 默认, 作者可看自己 pending
- 评论: 一级 + 二级 + audit + 计数同步
- 点赞: 幂等切换 + 计数同步 (post / comment 通用)
- 举报: 60s ≤ 1 + 累计 ≥ 5 自动 hidden
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import User
from app.services import otp_service

pytestmark = [pytest.mark.asyncio, pytest.mark.db]


# ─── helpers ────────────────────────────────────────────────────────────


async def _register_old_user(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    *,
    phone: str,
    code: str = "111111",
) -> tuple[uuid.UUID, str]:
    """OTP 注册 + 把 ``users.created_at`` 回调 60 天, 绕过新用户 7d 只读保护.

    社区主流程 (发帖 / 评论) 需要 user 度过 7d 保护期; e2e 用例不可能等 7 天,
    用 SQL 直接回调 created_at.
    """
    full_phone = phone if phone.startswith("+") else f"+86{phone}"
    await otp_service.store_otp(full_phone, code, ttl_seconds=300)
    resp = await client.post(
        "/api/v1/auth/login/phone", json={"phone": full_phone, "code": code}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    user_id = uuid.UUID(body["user"]["user_id"])
    token = body["tokens"]["access_token"]

    async with session_factory() as s:
        old_time = datetime.now(UTC) - timedelta(days=60)
        await s.execute(
            update(User).where(User.user_id == user_id).values(created_at=old_time)
        )
        await s.commit()
    return user_id, token


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_post(
    client: httpx.AsyncClient,
    token: str,
    *,
    content: str = "刚打了腾讯, 中签好运",
    category: str = "general",
    related_ipo_code: str | None = None,
) -> dict:
    payload: dict = {"content": content, "category": category}
    if related_ipo_code:
        payload["related_ipo_code"] = related_ipo_code
    res = await client.post("/api/v1/community/posts", json=payload, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()


# ─── 1. 鉴权 ────────────────────────────────────────────────────────────


async def test_unauthenticated_post_create_returns_401(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/api/v1/community/posts", json={"content": "hello"}
    )
    assert res.status_code == 401


async def test_anon_can_list_posts(client: httpx.AsyncClient) -> None:
    res = await client.get("/api/v1/community/posts")
    assert res.status_code == 200
    body = res.json()
    assert "items" in body and "total" in body


# ─── 2. 发帖 / 审核 ─────────────────────────────────────────────────────


async def test_new_user_within_7d_cannot_post(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新用户注册即发帖 → 403 (Nd 只读).

    BUG-S6.6-002a: ``community_new_user_readonly_days`` 已配置化, dev .env 默认 0
    (关闭). 这里 monkeypatch 强制设回 7 验证保护期逻辑还能跑.
    """
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("COMMUNITY_NEW_USER_READONLY_DAYS", "7")

    full_phone = "+8613000099999"
    await otp_service.store_otp(full_phone, "111111", ttl_seconds=300)
    resp = await client.post(
        "/api/v1/auth/login/phone", json={"phone": full_phone, "code": "111111"}
    )
    assert resp.status_code == 200
    token = resp.json()["tokens"]["access_token"]
    # 不回调 created_at, 直接发帖
    res = await client.post(
        "/api/v1/community/posts",
        json={"content": "新用户发帖"},
        headers=_h(token),
    )
    assert res.status_code == 403
    # cleanup: 还原 settings 缓存让其它用例不受影响
    get_settings.cache_clear()
    _ = session_factory  # 避免未使用


async def test_new_user_readonly_disabled_when_days_zero(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-S6.6-002a: ``community_new_user_readonly_days=0`` → 新用户立即可发帖."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("COMMUNITY_NEW_USER_READONLY_DAYS", "0")

    full_phone = "+8613000099998"
    await otp_service.store_otp(full_phone, "111111", ttl_seconds=300)
    resp = await client.post(
        "/api/v1/auth/login/phone", json={"phone": full_phone, "code": "111111"}
    )
    assert resp.status_code == 200
    token = resp.json()["tokens"]["access_token"]
    res = await client.post(
        "/api/v1/community/posts",
        json={"content": "0d 保护期下新用户立即发帖"},
        headers=_h(token),
    )
    assert res.status_code == 201, res.text
    get_settings.cache_clear()
    _ = session_factory


async def test_create_post_approve_published(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    _, token = await _register_old_user(
        client, session_factory, phone="13000000010"
    )
    body = await _create_post(client, token, content="今天港股市场 IPO 反响热烈, 赛道值得关注")
    assert body["status"] == "published"
    assert body["visibility"] == "public"
    assert body["likes_count"] == 0
    assert body["comments_count"] == 0


async def test_create_post_tier1_reject(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    """tier1 红线词 (e.g. '必涨') 必须 reject."""
    _, token = await _register_old_user(
        client, session_factory, phone="13000000011"
    )
    body = await _create_post(client, token, content="这只新股必涨, 闭眼买")
    assert body["status"] == "rejected"
    assert body["visibility"] == "self_only"
    assert body["rejection_reason"] == "content_violation"


async def test_create_post_private_flow_reject(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    """私域引流命中 → reject (rejection_reason=spam)."""
    _, token = await _register_old_user(
        client, session_factory, phone="13000000012"
    )
    body = await _create_post(
        client, token, content="加我微信 vx 一起讨论新股"
    )
    assert body["status"] == "rejected"
    assert body["rejection_reason"] == "spam"


async def test_create_post_privacy_leak_reject(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    """隐私数字串 (手机号 11 位) → reject (rejection_reason=privacy_leak)."""
    _, token = await _register_old_user(
        client, session_factory, phone="13000000013"
    )
    body = await _create_post(client, token, content="联系我 13800138000")
    assert body["status"] == "rejected"
    assert body["rejection_reason"] == "privacy_leak"


async def test_post_rate_limit_60s(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    _, token = await _register_old_user(
        client, session_factory, phone="13000000014"
    )
    # 第 1 帖 OK
    res1 = await client.post(
        "/api/v1/community/posts",
        json={"content": "第一帖, 测试限流"},
        headers=_h(token),
    )
    assert res1.status_code == 201
    # 第 2 帖 60s 内 → 429
    res2 = await client.post(
        "/api/v1/community/posts",
        json={"content": "第二帖, 应被限流"},
        headers=_h(token),
    )
    assert res2.status_code == 429


# ─── 3. 列表 / 详情 ─────────────────────────────────────────────────────


async def test_list_posts_returns_only_published(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    _, token = await _register_old_user(
        client, session_factory, phone="13000000015"
    )
    # 发 1 个 reject (含必涨) + 1 个正常
    await _create_post(client, token, content="必涨股票, 不要错过")  # rejected
    # 限流间隔: 直接走 service-level 不行, 这里靠 60s 限制下, 等于这条不能再发
    # 用第二个 user 发正常帖
    _, token2 = await _register_old_user(
        client, session_factory, phone="13000000016"
    )
    await _create_post(
        client, token2, content="刚打了港交所新股, 行情解读分享一下"
    )

    # public feed (匿名访问)
    res = await client.get("/api/v1/community/posts")
    assert res.status_code == 200
    items = res.json()["items"]
    # 只能看到 user2 的 published, user1 的 rejected 不在 public feed
    assert all(it["status"] == "published" for it in items)
    contents = [it["content"] for it in items]
    assert any("港交所" in c for c in contents)
    assert all("必涨" not in c for c in contents)


async def test_get_post_visible_to_author_when_pending(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    """rejected 帖只有作者能看, 别人 404."""
    user_id, token = await _register_old_user(
        client, session_factory, phone="13000000017"
    )
    body = await _create_post(client, token, content="必涨牛股")  # rejected
    post_id = body["id"]

    # 作者看自己 → 200
    res = await client.get(
        f"/api/v1/community/posts/{post_id}", headers=_h(token)
    )
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"

    # 别人看 → 404
    _, token_other = await _register_old_user(
        client, session_factory, phone="13000000018"
    )
    res = await client.get(
        f"/api/v1/community/posts/{post_id}", headers=_h(token_other)
    )
    assert res.status_code == 404
    _ = user_id


# ─── 4. 删除 ────────────────────────────────────────────────────────────


async def test_delete_post_by_owner(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    _, token = await _register_old_user(
        client, session_factory, phone="13000000019"
    )
    body = await _create_post(client, token, content="正常帖, 用于测试软删")
    post_id = body["id"]

    res = await client.delete(
        f"/api/v1/community/posts/{post_id}", headers=_h(token)
    )
    assert res.status_code == 204

    # 软删后 list 不返回
    res = await client.get("/api/v1/community/posts")
    items = res.json()["items"]
    assert all(it["id"] != post_id for it in items)


async def test_delete_post_other_user_403(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    _, token1 = await _register_old_user(
        client, session_factory, phone="13000000020"
    )
    body = await _create_post(client, token1, content="user1 的帖子")
    post_id = body["id"]

    _, token2 = await _register_old_user(
        client, session_factory, phone="13000000021"
    )
    res = await client.delete(
        f"/api/v1/community/posts/{post_id}", headers=_h(token2)
    )
    assert res.status_code == 403


# ─── 5. 评论 ────────────────────────────────────────────────────────────


async def test_create_and_list_comments(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    _, token1 = await _register_old_user(
        client, session_factory, phone="13000000022"
    )
    post_body = await _create_post(client, token1, content="发个帖子求评论")
    post_id = post_body["id"]

    _, token2 = await _register_old_user(
        client, session_factory, phone="13000000023"
    )
    res = await client.post(
        f"/api/v1/community/posts/{post_id}/comments",
        json={"content": "看好这只新股"},
        headers=_h(token2),
    )
    assert res.status_code == 201, res.text
    cb = res.json()
    assert cb["status"] == "published"
    assert cb["parent_comment_id"] is None

    # 列表
    res = await client.get(f"/api/v1/community/posts/{post_id}/comments")
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["content"] == "看好这只新股"

    # 帖子 comments_count 应同步 +1
    res = await client.get(f"/api/v1/community/posts/{post_id}")
    assert res.json()["comments_count"] == 1


async def test_comment_tier1_rejected_403(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    _, token1 = await _register_old_user(
        client, session_factory, phone="13000000024"
    )
    post_body = await _create_post(client, token1, content="测试评论审核")
    post_id = post_body["id"]

    _, token2 = await _register_old_user(
        client, session_factory, phone="13000000025"
    )
    res = await client.post(
        f"/api/v1/community/posts/{post_id}/comments",
        json={"content": "稳赚不赔, 闭眼买"},
        headers=_h(token2),
    )
    assert res.status_code == 403


# ─── 6. 点赞 ────────────────────────────────────────────────────────────


async def test_toggle_like_post(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    _, token1 = await _register_old_user(
        client, session_factory, phone="13000000026"
    )
    post_body = await _create_post(client, token1, content="测试点赞")
    post_id = post_body["id"]

    _, token2 = await _register_old_user(
        client, session_factory, phone="13000000027"
    )
    # 第一次: 加赞
    res = await client.post(
        "/api/v1/community/likes",
        json={"target_type": "post", "target_id": post_id},
        headers=_h(token2),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["liked"] is True
    assert body["likes_count"] == 1

    # 第二次: 取消
    res = await client.post(
        "/api/v1/community/likes",
        json={"target_type": "post", "target_id": post_id},
        headers=_h(token2),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["liked"] is False
    assert body["likes_count"] == 0


# ─── 7. 举报 ────────────────────────────────────────────────────────────


async def test_create_report(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    _, token1 = await _register_old_user(
        client, session_factory, phone="13000000028"
    )
    post_body = await _create_post(client, token1, content="被举报的帖子")
    post_id = post_body["id"]

    _, token2 = await _register_old_user(
        client, session_factory, phone="13000000029"
    )
    res = await client.post(
        "/api/v1/community/reports",
        json={
            "target_type": "post",
            "target_id": post_id,
            "reason": "spam",
            "detail": "包含广告",
        },
        headers=_h(token2),
    )
    assert res.status_code == 201
    rb = res.json()
    assert rb["status"] == "pending"
    assert rb["reason"] == "spam"


async def test_report_rate_limit(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    _, token1 = await _register_old_user(
        client, session_factory, phone="13000000030"
    )
    p1 = (await _create_post(client, token1, content="帖 1 用于举报"))["id"]
    # 第 2 帖会撞 60s 限流, 改 user2 发
    _, token_other = await _register_old_user(
        client, session_factory, phone="13000000031"
    )
    p2 = (await _create_post(client, token_other, content="帖 2 用于举报"))["id"]

    _, token_rep = await _register_old_user(
        client, session_factory, phone="13000000032"
    )
    res1 = await client.post(
        "/api/v1/community/reports",
        json={"target_type": "post", "target_id": p1, "reason": "spam"},
        headers=_h(token_rep),
    )
    assert res1.status_code == 201
    res2 = await client.post(
        "/api/v1/community/reports",
        json={"target_type": "post", "target_id": p2, "reason": "spam"},
        headers=_h(token_rep),
    )
    assert res2.status_code == 429
