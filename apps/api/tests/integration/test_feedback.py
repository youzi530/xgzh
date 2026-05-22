"""BE-S5-004 反馈端到端集成测.

覆盖 (spec/12 §AC):
1.  匿名用户 POST /feedback → 201, 入库, admin 列表能拉到
2.  登录用户 POST /feedback → 201, user_id 落库
3.  字段校验: category 不在枚举 / content 超长 → 422
4.  匿名 IP 限流: 4 次 → 第 4 次 429 + Retry-After header
5.  admin GET /feedbacks 分页 + filter by category / platform
6.  admin GET /feedbacks 缺 X-Admin-Token → 401
7.  red word 出现在 content: 仍 201 (不阻断), admin 看到原文
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import get_settings

pytestmark = [pytest.mark.asyncio, pytest.mark.db]


ADMIN_TOKEN = "test-admin-token-feedback-32-bytes"


@pytest.fixture(autouse=True)
def _set_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_ADMIN_TOKEN", ADMIN_TOKEN)
    get_settings.cache_clear()


# ─── 1. 匿名提交 ────────────────────────────────────────────────────


async def test_anon_post_feedback_succeeds(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/api/v1/feedback",
        json={
            "category": "bug",
            "content": "新股详情页加载半天空白, iPhone 15 Safari.",
            "contact": "wechat-test",
            "app_version": "1.0.0-h5",
            "platform": "h5",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert "feedback_id" in body
    assert "created_at" in body

    # admin 列表能拉到
    list_res = await client.get(
        "/api/v1/admin/ops/feedbacks", headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    assert list_res.status_code == 200
    items = list_res.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["feedback_id"] == body["feedback_id"]
    assert item["category"] == "bug"
    assert item["platform"] == "h5"
    assert item["user_id"] is None  # 匿名


# ─── 2. 字段校验 ────────────────────────────────────────────────────


async def test_invalid_category_rejected(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/api/v1/feedback",
        json={
            "category": "spam",  # 不在 Literal 里
            "content": "x",
            "platform": "h5",
        },
    )
    assert res.status_code == 422


async def test_content_too_long_rejected(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/api/v1/feedback",
        json={
            "category": "bug",
            "content": "a" * 2001,  # > 2000
            "platform": "h5",
        },
    )
    assert res.status_code == 422


async def test_invalid_platform_rejected(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/api/v1/feedback",
        json={
            "category": "bug",
            "content": "x",
            "platform": "windows-phone",  # 不在 Literal 里
        },
    )
    assert res.status_code == 422


# ─── 3. 限流 ────────────────────────────────────────────────────────


async def test_anon_ip_rate_limit_3_per_5min(client: httpx.AsyncClient) -> None:
    """匿名同 IP 第 4 次 → 429 + Retry-After header."""
    payload = {
        "category": "feature",
        "content": "希望加暗色模式",
        "platform": "h5",
    }
    # 因为单测走 ASGI Transport, request.client.host 总是 "testclient"
    # 即同 IP 下连发 3 次, 第 4 次必 429
    for i in range(3):
        res = await client.post("/api/v1/feedback", json=payload)
        assert res.status_code == 201, f"i={i} unexpected {res.status_code}: {res.text}"

    res4 = await client.post("/api/v1/feedback", json=payload)
    assert res4.status_code == 429
    assert "retry-after" in {k.lower() for k in res4.headers}
    body = res4.json()
    assert body["detail"]["code"] == "too_many_requests"


# ─── 4. admin 过滤 / 分页 ──────────────────────────────────────────


async def test_admin_list_filter_by_category_and_platform(
    client: httpx.AsyncClient,
) -> None:
    """admin 拉清单可用 category / platform filter, 分页准确."""
    base = {
        "content": "filter test",
    }
    posts = [
        {**base, "category": "bug", "platform": "h5"},
        {**base, "category": "bug", "platform": "mp-weixin"},
        {**base, "category": "feature", "platform": "h5"},
    ]
    # 用不同 X-Forwarded-For 让限流不挡 (3 次到匿名 IP 限流上限)
    for i, p in enumerate(posts):
        res = await client.post(
            "/api/v1/feedback",
            json=p,
            headers={"X-Forwarded-For": f"10.0.0.{i + 1}"},
        )
        assert res.status_code == 201, res.text

    h = {"X-Admin-Token": ADMIN_TOKEN}

    # 全列: 3 条
    res = await client.get("/api/v1/admin/ops/feedbacks", headers=h)
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert body["limit"] == 20
    assert body["offset"] == 0

    # filter category=bug → 2 条
    res = await client.get(
        "/api/v1/admin/ops/feedbacks?category=bug", headers=h
    )
    assert res.status_code == 200
    assert res.json()["total"] == 2

    # filter platform=h5 → 2 条 (bug+h5, feature+h5)
    res = await client.get(
        "/api/v1/admin/ops/feedbacks?platform=h5", headers=h
    )
    assert res.status_code == 200
    assert res.json()["total"] == 2

    # filter 双条件 category=bug AND platform=h5 → 1 条
    res = await client.get(
        "/api/v1/admin/ops/feedbacks?category=bug&platform=h5", headers=h
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["category"] == "bug"
    assert body["items"][0]["platform"] == "h5"

    # 分页 limit=1: 第二页能拿到
    res = await client.get(
        "/api/v1/admin/ops/feedbacks?limit=1&offset=1", headers=h
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1
    assert body["limit"] == 1
    assert body["offset"] == 1


# ─── 5. admin 鉴权 ──────────────────────────────────────────────────


async def test_admin_feedbacks_requires_token(client: httpx.AsyncClient) -> None:
    res = await client.get("/api/v1/admin/ops/feedbacks")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "admin_token_invalid"


async def test_admin_feedbacks_wrong_token(client: httpx.AsyncClient) -> None:
    res = await client.get(
        "/api/v1/admin/ops/feedbacks", headers={"X-Admin-Token": "wrong"}
    )
    assert res.status_code == 401


# ─── 6. 红线词不阻断 (用户有权吐槽 AI 说了必涨) ──────────────────────────


async def test_red_word_in_content_does_not_block(client: httpx.AsyncClient) -> None:
    """spec/12 §BE-S5-004 实现要点: content 含 Tier 1 词仍 201, admin 看到原文.

    用户提反馈说"你们 AI 说了必涨结果亏了" — 这是合法吐槽, 不应被合规模块阻断.
    """
    res = await client.post(
        "/api/v1/feedback",
        json={
            "category": "content",
            "content": "你们 AI 说这只新股必涨, 结果首日跌 8%",
            "platform": "h5",
        },
    )
    assert res.status_code == 201

    list_res = await client.get(
        "/api/v1/admin/ops/feedbacks", headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    items = list_res.json()["items"]
    assert len(items) == 1
    # admin 看到原文 (含红线词原样, 没被替换), 方便定位 LLM 是否真说错
    assert "必涨" in items[0]["content"]
