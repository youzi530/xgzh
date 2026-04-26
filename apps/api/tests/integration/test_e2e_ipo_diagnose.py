"""QA-001: 一条端到端打通 6 个模块的"金线"测试.

用例覆盖:
1. 入库一只 A 股 IPO (ipo_ingest_service.upsert_ipos)
2. OTP 发码 + 手机号登录 (BE-001 + BE-002, 拿 access/refresh)
3. 用 access_token 调 ``GET /me`` 验证鉴权链 (BE-005, 检查 invite_code 已生成)
4. 调 ``GET /api/v1/ipos`` 列表 (BE-008, 校验刚 seed 的 IPO 出现在列表里)
5. 调 ``GET /api/v1/ipos/{code}`` 详情 (BE-009, 校验深度字段结构)
6. 调 ``POST /api/v1/agent/diagnose`` SSE (BE / 现 agent 路由):
   - SSE body 含 ``event: start`` + ``found_in_source: true``
   - 至少 1 条 ``event: delta`` 帧带 mock token 内容
   - SSE body 含 ``event: end`` + ``ok: true``
   - SSE body 含合规免责声明 ("不构成投资建议")
7. 顺手 +关注 → /favorites 列表 → 再 -关注, 验证 BE-010 闭环

设计取舍:
- 不做"网络真打 LLM": ``fake_llm`` fixture 已替换 ``stream_chat`` 为固定 token,
  测试是 deterministic 的; 验"协议契约 + 数据透传", 不验模型质量
- 不做 SSE 客户端真流式消费: httpx ASGITransport 下 ``EventSourceResponse``
  会 buffer 全部 chunks 后返回 (因为 streaming 完成才关闭), 直接读 body 然后
  按 ``\\n\\n`` split SSE 帧已足够; 真流式断流测试在 Sprint 2 RAG 那里再加
- 不重复测各模块的边角错误码 (那些在各 ``test_*.py`` 里): 只测一条主路径
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.schemas.ipo import IPOItem
from app.services import ipo_ingest_service, otp_service

# ─── 帮助函数 ──────────────────────────────────────────────


def _seed_ipo() -> IPOItem:
    """金线测试用的 fixture IPO: 一只确定性 A 股, 字段全填好."""
    return IPOItem(
        code="600519.SH",
        name="贵州茅台",
        market="A",
        industry="食品饮料",
        issue_price=Decimal("1499.00"),
        issue_currency="CNY",
        listing_date=date(2001, 8, 27),
        pe_ratio=Decimal("28.50"),
        raised_amount=Decimal("2000000000"),
        one_lot_winning_rate=Decimal("0.0850"),
        status="listed",
        data_source="e2e-fixture",
        updated_at=datetime.now(UTC).replace(tzinfo=None),
    )


def _parse_sse_frames(body: str) -> list[tuple[str, dict[str, object]]]:
    """把 SSE 响应 body 切成 ``(event_type, parsed_data_dict)`` 列表.

    SSE 帧格式: 每帧由 ``event: <name>\\ndata: <json>\\n\\n`` 组成;
    用 ``\\n\\n`` (注: 实际是 CRLF 或 LF, sse-starlette 用 \\r\\n\\r\\n) split,
    然后逐行解析。
    """
    # sse-starlette 默认 \r\n 分隔符; httpx 拿到的是 bytes decode 后的 str
    body_norm = body.replace("\r\n", "\n")
    frames: list[tuple[str, dict[str, object]]] = []
    for chunk in body_norm.split("\n\n"):
        if not chunk.strip():
            continue
        event_type = ""
        data_str = ""
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_str = line[len("data:") :].strip()
        if not event_type:
            continue
        try:
            parsed = json.loads(data_str) if data_str else {}
        except json.JSONDecodeError:
            parsed = {"_raw": data_str}
        frames.append((event_type, parsed))
    return frames


# ─── 主用例 ──────────────────────────────────────────────


async def test_e2e_register_to_diagnose_to_favorite(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_llm: list[str],
) -> None:
    """金线 e2e: 注册 → token → /me → /ipos → /diagnose SSE → 收藏闭环."""

    # ─── 1. seed 一只 A 股 IPO 入库 ──────────────────
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(session, [_seed_ipo()])
        await session.commit()

    # ─── 2. OTP 发码 + 手机号登录 (BE-001 + BE-002) ──────────────────
    phone = "+8613800138999"
    code = "789012"
    # 直接埋 OTP, 跳过短信投递 (mock_sms fixture 已防真打)
    await otp_service.store_otp(phone, code, ttl_seconds=300)

    login_resp = await client.post(
        "/api/v1/auth/login/phone",
        json={"phone": phone, "code": code},
    )
    assert login_resp.status_code == 200, login_resp.text
    login_body = login_resp.json()
    access = login_body["tokens"]["access_token"]
    refresh = login_body["tokens"]["refresh_token"]
    assert access and refresh
    # spec/06 §2.3 隐私脱敏: UserPublic 不暴露 phone / wechat_openid; 用 user_id +
    # invite_code 作为身份标识
    user_id = login_body["user"]["user_id"]
    invite_code = login_body["user"]["invite_code"]
    assert user_id and invite_code, "新用户应自动生成 user_id + 邀请码 (BE-001)"
    assert login_body["is_new_user"] is True

    auth_h = {"Authorization": f"Bearer {access}"}

    # ─── 3. /me 鉴权链验证 (BE-005) ──────────────────
    me_resp = await client.get("/api/v1/me", headers=auth_h)
    assert me_resp.status_code == 200, me_resp.text
    me_body = me_resp.json()
    assert me_body["user_id"] == user_id, "/me 应返回同一 user"
    assert me_body["invite_code"] == invite_code
    assert "phone" not in me_body, "spec/06 §2.3 隐私脱敏: 不暴露手机号"

    # ─── 4. /ipos 列表 (BE-008) ──────────────────
    ipos_resp = await client.get("/api/v1/ipos?market=A", headers=auth_h)
    assert ipos_resp.status_code == 200, ipos_resp.text
    ipos_body = ipos_resp.json()
    assert ipos_body["market"] == "A"
    assert ipos_body["total"] >= 1
    seed = next((it for it in ipos_body["items"] if it["code"] == "600519.SH"), None)
    assert seed is not None, "刚 seed 的 600519.SH 应出现在列表"
    assert seed["name"] == "贵州茅台"
    assert seed["industry"] == "食品饮料"
    assert seed["status"] == "listed"
    assert seed["data_source"] == "e2e-fixture"

    # ─── 5. /ipos/{code} 详情 (BE-009) ──────────────────
    detail_resp = await client.get("/api/v1/ipos/600519.SH", headers=auth_h)
    assert detail_resp.status_code == 200, detail_resp.text
    detail_body = detail_resp.json()
    assert detail_body["code"] == "600519.SH"
    assert detail_body["name"] == "贵州茅台"
    # IPODetail extends IPOItem, 6 个深度字段必须出现 (即便为空)
    assert "highlights" in detail_body, "IPODetail 必须包含 highlights 字段"
    assert "risks" in detail_body, "IPODetail 必须包含 risks 字段"
    assert "sponsors" in detail_body
    assert "underwriters" in detail_body
    assert "prospectus_url" in detail_body
    assert "financial_summary" in detail_body

    # ─── 6. /agent/diagnose SSE (主线: 验合规 + 透传) ──────────────────
    diag_resp = await client.post(
        "/api/v1/agent/diagnose",
        json={"code": "600519.SH", "name": "贵州茅台"},
        headers=auth_h,
    )
    assert diag_resp.status_code == 200, diag_resp.text
    sse_body = diag_resp.text
    assert sse_body, "SSE 响应 body 不该为空"

    frames = _parse_sse_frames(sse_body)
    assert len(frames) >= 3, f"SSE 至少要有 start + delta + end 三帧, 实际 {len(frames)}"

    # 6a. start 帧
    start_frames = [f for f in frames if f[0] == "start"]
    assert len(start_frames) == 1, "必须恰好一个 start 帧"
    start_data = start_frames[0][1]
    assert start_data["code"] == "600519.SH"
    assert start_data["name"] == "贵州茅台"
    assert start_data["found_in_source"] is True, "DB 已 seed, 应命中"

    # 6b. delta 帧透传 (来自 fake_llm 的固定 token 序列)
    delta_frames = [f for f in frames if f[0] == "delta"]
    assert len(delta_frames) >= len(fake_llm), (
        f"delta 帧数应 >= fake token 数 {len(fake_llm)} (含可能的 disclaimer 帧), "
        f"实际 {len(delta_frames)}"
    )
    delta_contents = "".join(str(f[1].get("content", "")) for f in delta_frames)
    # 至少一个 fake token 应在 delta 流里能拼出来
    assert "基本面摘要" in delta_contents, "fake token 应被透传到 SSE delta"
    assert "估值偏高" in delta_contents

    # 6c. end 帧
    end_frames = [f for f in frames if f[0] == "end"]
    assert len(end_frames) == 1, "必须恰好一个 end 帧"
    assert end_frames[0][1].get("ok") is True

    # 6d. 没有 error 帧 (LLM mock 路径不该报错)
    error_frames = [f for f in frames if f[0] == "error"]
    assert error_frames == [], f"不应出现 error 帧, 实际: {error_frames}"

    # 6e. 合规护栏: SSE 流里必含免责声明 (来自 fake_llm 末尾的 DISCLAIMER)
    full_text = sse_body  # 包括所有 SSE 帧的原文
    assert "不构成投资建议" in full_text, (
        "spec/06 §法律隔离 硬要求: AI 输出末尾必含 '不构成投资建议' 免责声明"
    )

    # ─── 7. 收藏闭环 (BE-010) ──────────────────
    add_resp = await client.post(
        "/api/v1/favorites",
        json={"code": "600519.SH"},
        headers=auth_h,
    )
    assert add_resp.status_code == 200, add_resp.text
    assert add_resp.json()["created"] is True
    assert add_resp.json()["market"] == "A"

    list_resp = await client.get("/api/v1/favorites", headers=auth_h)
    assert list_resp.status_code == 200
    fav_body = list_resp.json()
    assert fav_body["total"] == 1
    fav = fav_body["items"][0]
    assert fav["code"] == "600519.SH"
    assert fav["name"] == "贵州茅台", "LEFT JOIN ipos 应拿到 name"
    assert fav["status"] == "listed"

    del_resp = await client.delete("/api/v1/favorites/600519.SH", headers=auth_h)
    assert del_resp.status_code == 200
    assert del_resp.json()["removed"] is True

    list_after = (await client.get("/api/v1/favorites", headers=auth_h)).json()
    assert list_after["total"] == 0


# ─── 退化路径: agent diagnose 对未知 code 的兜底 ──────────────────


async def test_e2e_diagnose_unknown_code_still_returns_sse(
    client: httpx.AsyncClient,
    fake_llm: list[str],  # noqa: ARG001
) -> None:
    """code 没在 ipos 表 / HK seed 命中时, agent 仍能 SSE 出 'found_in_source=False'.

    体现 spec/04 §1.3 "匿名 / 未知 IPO 也能用 AI" 设计原则: 这里我们不做匿名,
    只验证 code 不命中时的兜底 (start.found_in_source=False + 仍有 delta + end + 免责)。
    """
    phone = "+8613800138001"
    code = "234567"
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    login = await client.post(
        "/api/v1/auth/login/phone", json={"phone": phone, "code": code}
    )
    assert login.status_code == 200
    h = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}

    diag = await client.post(
        "/api/v1/agent/diagnose",
        json={"code": "9999.UNKNOWN", "name": "不存在的股票"},
        headers=h,
    )
    assert diag.status_code == 200
    frames = _parse_sse_frames(diag.text)

    start = next(f for f in frames if f[0] == "start")
    assert start[1]["code"] == "9999.UNKNOWN"
    assert start[1]["found_in_source"] is False, "未知 code 应在 start 帧标 found_in_source=False"

    assert any(f[0] == "delta" for f in frames), "兜底路径仍要有 delta"
    assert any(f[0] == "end" for f in frames), "兜底路径仍要有 end"
    assert "不构成投资建议" in diag.text, "兜底路径合规免责仍需出现"


# ─── 鉴权护栏: agent /diagnose 当前是否要求登录? ──────────────────


async def test_e2e_diagnose_anonymous_allowed(
    client: httpx.AsyncClient,
    fake_llm: list[str],  # noqa: ARG001
) -> None:
    """spec/04 §1.3 设计为"匿名也能用 AI"; 当前 ``/agent/diagnose`` 不挂 ``get_current_user``.

    这条用例当前 PASS = 默认行为; 如果将来加配额限制改成"登录强制 + 配额", 这条会
    fail, 提醒同步更新 spec 文档。
    """
    diag = await client.post(
        "/api/v1/agent/diagnose",
        json={"code": "AAPL.US", "name": "Apple Inc."},
    )
    assert diag.status_code == 200, (
        "spec/04 §1.3 当前允许匿名调用 /agent/diagnose; "
        "若改为登录强制需同步更新这条用例和 spec 文档"
    )
    assert "不构成投资建议" in diag.text
