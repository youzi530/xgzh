"""OPS-S4-001 admin API 集成测.

覆盖 (spec/11 §OPS-S4-001 AC):
1.  ``OPS_ADMIN_TOKEN`` 未配置 → 503 admin_disabled
2.  X-Admin-Token 缺失 → 401
3.  X-Admin-Token 错 → 401
4.  X-Admin-Token 正确 → 200, 流程通
5.  GET /admin/flags 列出已写 flag
6.  PUT /admin/flags/{name} upsert + 钳值
7.  GET /admin/flags/{name} 不存在 → 404
8.  DELETE /admin/flags/{name} 流程
9.  GET /admin/metrics 反映中间件累计的 request/error 计数
10. POST /admin/metrics/reset 清零
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import get_settings

pytestmark = [pytest.mark.asyncio, pytest.mark.db]


ADMIN_TOKEN = "test-admin-token-32-bytes-random-1234"


@pytest.fixture(autouse=True)
def _set_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """大部分测试都要 token 已配置. 不需要的子用例自己 monkeypatch.delenv."""
    monkeypatch.setenv("OPS_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ERROR_ALERT_WINDOW_SECONDS", "60")
    get_settings.cache_clear()


# ─── 鉴权护栏 ─────────────────────────────────────────────────────


async def test_admin_disabled_when_token_unset(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPS_ADMIN_TOKEN", "")
    get_settings.cache_clear()
    res = await client.get("/api/v1/admin/flags", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert res.status_code == 503
    body = res.json()
    assert body["detail"]["code"] == "admin_disabled"


async def test_admin_unauthorized_without_header(client: httpx.AsyncClient) -> None:
    res = await client.get("/api/v1/admin/flags")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "admin_token_invalid"


async def test_admin_unauthorized_with_wrong_token(client: httpx.AsyncClient) -> None:
    res = await client.get(
        "/api/v1/admin/flags", headers={"X-Admin-Token": "wrong-token"}
    )
    assert res.status_code == 401


# ─── Flags CRUD ───────────────────────────────────────────────────


async def test_flags_full_lifecycle(client: httpx.AsyncClient) -> None:
    h = {"X-Admin-Token": ADMIN_TOKEN}

    # 初态: 空
    res = await client.get("/api/v1/admin/flags", headers=h)
    assert res.status_code == 200
    assert res.json() == {"flags": []}

    # PUT 创建
    res = await client.put(
        "/api/v1/admin/flags/history_tab",
        headers=h,
        json={"enabled": True, "rollout_pct": 5},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "history_tab"
    assert body["enabled"] is True
    assert body["rollout_pct"] == 5
    assert "updated_at" in body

    # GET 单条
    res = await client.get("/api/v1/admin/flags/history_tab", headers=h)
    assert res.status_code == 200
    assert res.json()["rollout_pct"] == 5

    # PUT 更新
    res = await client.put(
        "/api/v1/admin/flags/history_tab",
        headers=h,
        json={"enabled": True, "rollout_pct": 25},
    )
    assert res.status_code == 200
    assert res.json()["rollout_pct"] == 25

    # 列表 = 1
    res = await client.get("/api/v1/admin/flags", headers=h)
    assert res.status_code == 200
    flags = res.json()["flags"]
    assert len(flags) == 1
    assert flags[0]["name"] == "history_tab"

    # DELETE
    res = await client.delete("/api/v1/admin/flags/history_tab", headers=h)
    assert res.status_code == 204

    # 删完 GET 单 → 404
    res = await client.get("/api/v1/admin/flags/history_tab", headers=h)
    assert res.status_code == 404


async def test_flag_get_unknown_returns_404(client: httpx.AsyncClient) -> None:
    res = await client.get(
        "/api/v1/admin/flags/nonexistent",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "flag_not_found"


async def test_flag_delete_unknown_returns_404(client: httpx.AsyncClient) -> None:
    res = await client.delete(
        "/api/v1/admin/flags/nonexistent",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert res.status_code == 404


async def test_flag_rollout_pct_validation(client: httpx.AsyncClient) -> None:
    """``rollout_pct`` 走 pydantic ``ge=0, le=100``: 越界返 422."""
    h = {"X-Admin-Token": ADMIN_TOKEN}
    res = await client.put(
        "/api/v1/admin/flags/x",
        headers=h,
        json={"enabled": True, "rollout_pct": 150},
    )
    assert res.status_code == 422


# ─── Metrics ──────────────────────────────────────────────────────


async def test_metrics_reflects_middleware_recording(client: httpx.AsyncClient) -> None:
    """打几次 ``/healthz`` 让中间件记录, 然后 ``/admin/metrics`` 应该看到."""
    for _ in range(5):
        await client.get("/healthz")
    res = await client.get(
        "/api/v1/admin/metrics", headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    assert res.status_code == 200
    body = res.json()
    # 自身这条 admin/metrics 也算一次, 所以 >= 5 + 1
    assert body["total_requests"] >= 5
    assert body["total_errors"] == 0
    assert body["error_pct"] == 0.0
    assert body["window_seconds"] == 60


async def test_metrics_reset_clears(client: httpx.AsyncClient) -> None:
    h = {"X-Admin-Token": ADMIN_TOKEN}
    for _ in range(3):
        await client.get("/healthz")
    res = await client.post("/api/v1/admin/metrics/reset", headers=h)
    assert res.status_code == 204

    # reset 后再读 (admin/metrics 这条也算 1, 所以 = 1)
    res = await client.get("/api/v1/admin/metrics", headers=h)
    assert res.status_code == 200
    body = res.json()
    assert body["total_requests"] == 1
    assert body["total_errors"] == 0
