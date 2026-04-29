"""BE-S6-002 中签记账端到端集成测.

覆盖 (spec/13 §BE-S6-002 AC):
- 账户 CRUD: 创建 / 列表 / 改 / 删 + 跨用户 / 重名
- 主账户切换: is_primary=True 自动把其它账户置 false
- 中签 records CRUD: 录 / 列 / 改 / 删 + 跨用户
- PnL 自动算: 已卖出 (realized) / 持有 (unrealized) / 未中签 / 含孖展 / 含手续费
- 字段校验: ipo_code 大小写归一化 / allotted ≤ subscribe / region 枚举
- 限流: POST /accounts 60s ≤ 5, POST /subscriptions 60s ≤ 10
- 鉴权: 未登录全部 → 401
- 级联删: 删账户带走 records
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.services import otp_service

pytestmark = [pytest.mark.asyncio, pytest.mark.db]


# ─── helpers ────────────────────────────────────────────────────────────


async def _register_via_otp(
    client: httpx.AsyncClient, *, phone: str, code: str = "111111"
) -> tuple[uuid.UUID, str]:
    """OTP 注册 + 登录, 返 (user_id, access_token). phone 自动加 +86 前缀."""
    full_phone = phone if phone.startswith("+") else f"+86{phone}"
    await otp_service.store_otp(full_phone, code, ttl_seconds=300)
    resp = await client.post(
        "/api/v1/auth/login/phone", json={"phone": full_phone, "code": code}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return uuid.UUID(body["user"]["user_id"]), body["tokens"]["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_account(
    client: httpx.AsyncClient,
    token: str,
    *,
    label: str = "招商",
    region: str = "HK",
    is_primary: bool = False,
    broker_name: str | None = None,
) -> dict:
    payload: dict = {"label": label, "region": region, "is_primary": is_primary}
    if broker_name is not None:
        payload["broker_name"] = broker_name
    res = await client.post("/api/v1/subscriptions/accounts", json=payload, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()


# ─── 1. 鉴权 ────────────────────────────────────────────────────────────


async def test_unauthenticated_account_create_returns_401(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/api/v1/subscriptions/accounts",
        json={"label": "x", "region": "HK", "is_primary": False},
    )
    assert res.status_code == 401


async def test_unauthenticated_record_create_returns_401(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": str(uuid.uuid4()),
            "ipo_code": "00700",
            "region": "HK",
            "subscribe_shares": 100,
            "subscribed_at": "2026-04-01",
        },
    )
    assert res.status_code == 401


# ─── 2. 账户 CRUD ───────────────────────────────────────────────────────


async def test_create_account_basic(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000001")
    body = await _create_account(client, token, label="招商账户", region="HK")
    assert body["label"] == "招商账户"
    assert body["region"] == "HK"
    assert body["is_primary"] is False
    assert "id" in body and "created_at" in body


async def test_create_account_strips_label_whitespace(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000002")
    body = await _create_account(client, token, label="  华盛  ")
    assert body["label"] == "华盛"


async def test_create_account_duplicate_label_returns_409(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000003")
    await _create_account(client, token, label="同名")
    res = await client.post(
        "/api/v1/subscriptions/accounts",
        json={"label": "同名", "region": "HK", "is_primary": False},
        headers=_h(token),
    )
    assert res.status_code == 409


async def test_create_account_is_primary_demotes_existing_primary(
    client: httpx.AsyncClient,
) -> None:
    _, token = await _register_via_otp(client, phone="13000000004")
    a1 = await _create_account(client, token, label="A", is_primary=True)
    a2 = await _create_account(client, token, label="B", is_primary=True)

    list_res = await client.get("/api/v1/subscriptions/accounts", headers=_h(token))
    items = list_res.json()["items"]
    by_id = {it["id"]: it for it in items}
    assert by_id[a1["id"]]["is_primary"] is False
    assert by_id[a2["id"]]["is_primary"] is True


async def test_list_accounts_isolates_users(client: httpx.AsyncClient) -> None:
    _, t1 = await _register_via_otp(client, phone="13000000005")
    _, t2 = await _register_via_otp(client, phone="13000000006")
    await _create_account(client, t1, label="只属于 1")
    await _create_account(client, t2, label="只属于 2")

    res1 = await client.get("/api/v1/subscriptions/accounts", headers=_h(t1))
    res2 = await client.get("/api/v1/subscriptions/accounts", headers=_h(t2))
    labels1 = {it["label"] for it in res1.json()["items"]}
    labels2 = {it["label"] for it in res2.json()["items"]}
    assert labels1 == {"只属于 1"}
    assert labels2 == {"只属于 2"}


async def test_list_accounts_primary_first(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000007")
    await _create_account(client, token, label="X", is_primary=False)
    primary = await _create_account(client, token, label="Y", is_primary=True)
    res = await client.get("/api/v1/subscriptions/accounts", headers=_h(token))
    items = res.json()["items"]
    assert items[0]["id"] == primary["id"]


async def test_update_account_label(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000008")
    acc = await _create_account(client, token, label="旧名")
    res = await client.put(
        f"/api/v1/subscriptions/accounts/{acc['id']}",
        json={"label": "新名"},
        headers=_h(token),
    )
    assert res.status_code == 200
    assert res.json()["label"] == "新名"


async def test_update_account_cross_user_returns_404(client: httpx.AsyncClient) -> None:
    _, t1 = await _register_via_otp(client, phone="13000000009")
    _, t2 = await _register_via_otp(client, phone="13000000010")
    acc = await _create_account(client, t1, label="A")
    res = await client.put(
        f"/api/v1/subscriptions/accounts/{acc['id']}",
        json={"label": "X"},
        headers=_h(t2),
    )
    assert res.status_code == 404


async def test_delete_account_returns_204(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000011")
    acc = await _create_account(client, token, label="待删")
    res = await client.delete(
        f"/api/v1/subscriptions/accounts/{acc['id']}", headers=_h(token)
    )
    assert res.status_code == 204
    list_res = await client.get("/api/v1/subscriptions/accounts", headers=_h(token))
    assert list_res.json()["total"] == 0


async def test_delete_account_cross_user_returns_404(client: httpx.AsyncClient) -> None:
    _, t1 = await _register_via_otp(client, phone="13000000012")
    _, t2 = await _register_via_otp(client, phone="13000000013")
    acc = await _create_account(client, t1, label="x")
    res = await client.delete(
        f"/api/v1/subscriptions/accounts/{acc['id']}", headers=_h(t2)
    )
    assert res.status_code == 404


# ─── 3. 中签 records CRUD ──────────────────────────────────────────────


async def test_create_record_basic_pnl_unrealized(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000014")
    acc = await _create_account(client, token, label="A", region="HK")
    res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc["id"],
            "ipo_code": "00700",
            "region": "HK",
            "subscribe_shares": 1000,
            "allotted_shares": 100,
            "subscribe_price": "10.00",
            "fees": "5.00",
            "first_day_close": "12.00",
            "subscribed_at": "2026-04-01",
            "listed_at": "2026-04-10",
        },
        headers=_h(token),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    # (12 - 10) * 100 - 5 = 195
    assert body["unrealized_pnl"] == "195.00"
    assert body["realized_pnl"] is None


async def test_create_record_realized_with_sell(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000015")
    acc = await _create_account(client, token, label="A", region="HK")
    res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc["id"],
            "ipo_code": "00700",
            "region": "HK",
            "subscribe_shares": 1000,
            "allotted_shares": 100,
            "subscribe_price": "10.00",
            "fees": "5.00",
            "first_day_close": "12.00",
            "sell_price": "15.00",
            "sell_at": "2026-04-15T10:00:00+08:00",
            "subscribed_at": "2026-04-01",
            "listed_at": "2026-04-10",
        },
        headers=_h(token),
    )
    assert res.status_code == 201
    body = res.json()
    # realized = (15 - 10) * 100 - 5 = 495; unrealized still computed too
    assert body["realized_pnl"] == "495.00"
    assert body["unrealized_pnl"] == "195.00"


async def test_create_record_with_margin(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000016")
    acc = await _create_account(client, token, label="A", region="HK")
    res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc["id"],
            "ipo_code": "00700",
            "region": "HK",
            "subscribe_shares": 1000,
            "allotted_shares": 100,
            "subscribe_price": "10.00",
            "fees": "5.00",
            "margin_amount": "30.00",
            "first_day_close": "12.00",
            "subscribed_at": "2026-04-01",
        },
        headers=_h(token),
    )
    assert res.status_code == 201
    body = res.json()
    # (12 - 10) * 100 - 5 - 30 = 165
    assert body["unrealized_pnl"] == "165.00"


async def test_create_record_unallotted_pnl_null(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000017")
    acc = await _create_account(client, token, label="A")
    res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc["id"],
            "ipo_code": "00700",
            "region": "HK",
            "subscribe_shares": 1000,
            "allotted_shares": 0,  # 未中签
            "subscribe_price": "10.00",
            "first_day_close": "12.00",
            "subscribed_at": "2026-04-01",
        },
        headers=_h(token),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["unrealized_pnl"] is None
    assert body["realized_pnl"] is None


async def test_create_record_no_close_no_unrealized(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000018")
    acc = await _create_account(client, token, label="A")
    res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc["id"],
            "ipo_code": "00700",
            "region": "HK",
            "subscribe_shares": 1000,
            "allotted_shares": 100,
            "subscribe_price": "10.00",
            "subscribed_at": "2026-04-01",
        },
        headers=_h(token),
    )
    assert res.status_code == 201
    body = res.json()
    assert body["unrealized_pnl"] is None
    assert body["realized_pnl"] is None


async def test_create_record_ipo_code_normalized_uppercase(
    client: httpx.AsyncClient,
) -> None:
    _, token = await _register_via_otp(client, phone="13000000019")
    acc = await _create_account(client, token, label="A")
    res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc["id"],
            "ipo_code": "  00700.hk  ",
            "region": "HK",
            "subscribe_shares": 100,
            "subscribed_at": "2026-04-01",
        },
        headers=_h(token),
    )
    assert res.status_code == 201
    assert res.json()["ipo_code"] == "00700.HK"


async def test_create_record_allotted_gt_subscribe_rejected(
    client: httpx.AsyncClient,
) -> None:
    _, token = await _register_via_otp(client, phone="13000000020")
    acc = await _create_account(client, token, label="A")
    res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc["id"],
            "ipo_code": "00700",
            "region": "HK",
            "subscribe_shares": 100,
            "allotted_shares": 200,  # 不合理
            "subscribed_at": "2026-04-01",
        },
        headers=_h(token),
    )
    assert res.status_code == 422


async def test_create_record_invalid_region_rejected(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000021")
    acc = await _create_account(client, token, label="A")
    res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc["id"],
            "ipo_code": "00700",
            "region": "JP",  # 不在枚举
            "subscribe_shares": 100,
            "subscribed_at": "2026-04-01",
        },
        headers=_h(token),
    )
    assert res.status_code == 422


async def test_create_record_account_cross_user_returns_404(
    client: httpx.AsyncClient,
) -> None:
    _, t1 = await _register_via_otp(client, phone="13000000022")
    _, t2 = await _register_via_otp(client, phone="13000000023")
    acc1 = await _create_account(client, t1, label="A")
    res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc1["id"],
            "ipo_code": "00700",
            "region": "HK",
            "subscribe_shares": 100,
            "subscribed_at": "2026-04-01",
        },
        headers=_h(t2),
    )
    assert res.status_code == 404


async def test_list_records_filter_by_account(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000024")
    a1 = await _create_account(client, token, label="A1")
    a2 = await _create_account(client, token, label="A2")
    for code, acc in [("00001", a1), ("00002", a1), ("00003", a2)]:
        await client.post(
            "/api/v1/subscriptions",
            json={
                "account_id": acc["id"],
                "ipo_code": code,
                "region": "HK",
                "subscribe_shares": 100,
                "subscribed_at": "2026-04-01",
            },
            headers=_h(token),
        )

    res = await client.get(
        f"/api/v1/subscriptions?account_id={a1['id']}", headers=_h(token)
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert {it["ipo_code"] for it in body["items"]} == {"00001", "00002"}


async def test_list_records_sort_listed_at_desc_nulls_last(
    client: httpx.AsyncClient,
) -> None:
    """listed_at 有日期的排前, NULL 排后."""
    _, token = await _register_via_otp(client, phone="13000000025")
    acc = await _create_account(client, token, label="A")
    for code, listed in [
        ("0001", "2026-03-01"),
        ("0002", None),
        ("0003", "2026-04-15"),
    ]:
        payload: dict = {
            "account_id": acc["id"],
            "ipo_code": code,
            "region": "HK",
            "subscribe_shares": 100,
            "subscribed_at": "2026-02-01",
        }
        if listed is not None:
            payload["listed_at"] = listed
        await client.post("/api/v1/subscriptions", json=payload, headers=_h(token))

    res = await client.get("/api/v1/subscriptions", headers=_h(token))
    items = res.json()["items"]
    codes = [it["ipo_code"] for it in items]
    # 0003 (4-15) > 0001 (3-1) > 0002 (NULL)
    assert codes == ["0003", "0001", "0002"]


async def test_get_record_cross_user_returns_404(client: httpx.AsyncClient) -> None:
    _, t1 = await _register_via_otp(client, phone="13000000026")
    _, t2 = await _register_via_otp(client, phone="13000000027")
    acc = await _create_account(client, t1, label="A")
    create_res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc["id"],
            "ipo_code": "00700",
            "region": "HK",
            "subscribe_shares": 100,
            "subscribed_at": "2026-04-01",
        },
        headers=_h(t1),
    )
    rid = create_res.json()["id"]
    res = await client.get(f"/api/v1/subscriptions/{rid}", headers=_h(t2))
    assert res.status_code == 404


async def test_update_record_recompute_pnl(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000028")
    acc = await _create_account(client, token, label="A")
    create_res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc["id"],
            "ipo_code": "00700",
            "region": "HK",
            "subscribe_shares": 1000,
            "allotted_shares": 100,
            "subscribe_price": "10.00",
            "fees": "5.00",
            "first_day_close": "12.00",
            "subscribed_at": "2026-04-01",
        },
        headers=_h(token),
    )
    rid = create_res.json()["id"]
    # 用户后续补卖出价 → realized_pnl 自动算
    res = await client.put(
        f"/api/v1/subscriptions/{rid}",
        json={"sell_price": "20.00", "sell_at": "2026-04-20T10:00:00+08:00"},
        headers=_h(token),
    )
    assert res.status_code == 200
    body = res.json()
    # (20 - 10) * 100 - 5 = 995
    assert body["realized_pnl"] == "995.00"
    # unrealized 不变
    assert body["unrealized_pnl"] == "195.00"


async def test_delete_record_returns_204(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000029")
    acc = await _create_account(client, token, label="A")
    create_res = await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc["id"],
            "ipo_code": "00700",
            "region": "HK",
            "subscribe_shares": 100,
            "subscribed_at": "2026-04-01",
        },
        headers=_h(token),
    )
    rid = create_res.json()["id"]
    res = await client.delete(f"/api/v1/subscriptions/{rid}", headers=_h(token))
    assert res.status_code == 204
    list_res = await client.get("/api/v1/subscriptions", headers=_h(token))
    assert list_res.json()["total"] == 0


# ─── 4. 级联删 ───────────────────────────────────────────────────────────


async def test_delete_account_cascades_records(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000030")
    acc = await _create_account(client, token, label="A")
    await client.post(
        "/api/v1/subscriptions",
        json={
            "account_id": acc["id"],
            "ipo_code": "00700",
            "region": "HK",
            "subscribe_shares": 100,
            "subscribed_at": "2026-04-01",
        },
        headers=_h(token),
    )
    list_pre = await client.get("/api/v1/subscriptions", headers=_h(token))
    assert list_pre.json()["total"] == 1

    await client.delete(f"/api/v1/subscriptions/accounts/{acc['id']}", headers=_h(token))
    list_post = await client.get("/api/v1/subscriptions", headers=_h(token))
    assert list_post.json()["total"] == 0


# ─── 5. 限流 ────────────────────────────────────────────────────────────


async def test_account_create_rate_limit_5_per_minute(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000031")
    for i in range(5):
        res = await client.post(
            "/api/v1/subscriptions/accounts",
            json={"label": f"a{i}", "region": "HK", "is_primary": False},
            headers=_h(token),
        )
        assert res.status_code == 201, f"i={i} {res.text}"
    res6 = await client.post(
        "/api/v1/subscriptions/accounts",
        json={"label": "a6", "region": "HK", "is_primary": False},
        headers=_h(token),
    )
    assert res6.status_code == 429
    assert "Retry-After" in res6.headers


async def test_record_create_rate_limit_10_per_minute(client: httpx.AsyncClient) -> None:
    _, token = await _register_via_otp(client, phone="13000000032")
    acc = await _create_account(client, token, label="A")
    payload = {
        "account_id": acc["id"],
        "ipo_code": "00700",
        "region": "HK",
        "subscribe_shares": 100,
        "subscribed_at": "2026-04-01",
    }
    for i in range(10):
        res = await client.post(
            "/api/v1/subscriptions", json=payload, headers=_h(token)
        )
        assert res.status_code == 201, f"i={i} {res.text}"
    res11 = await client.post("/api/v1/subscriptions", json=payload, headers=_h(token))
    assert res11.status_code == 429
    assert "Retry-After" in res11.headers
