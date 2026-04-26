"""``POST /api/v1/chat/diagnose`` 配额闸门 E2E (BE-S2-008).

覆盖:
- **匿名超额 → 429**: ``anon_per_window=1``, 同一 IP 连调 2 次, 第 2 次直接拿到
  HTTP 429 + ``ChatQuotaExceededResponse`` body (含 quota.plan / used / remaining /
  retry_after_seconds), 第 1 次 SSE 正常走完
- **匿名第一次扣额 → 第二次入口前置闸门挡掉**: 验证 record_usage 真的写了 Redis
- **登录 FREE 用户超额 → 429 + plan="free"**: 1 次后即超额
- **登录 VIP 用户 → 不限流**: ``vip_user_id_whitelist`` 含 user, 调 N 次都 200
- **超额时 user_message 不应再落 chat_messages**: 入口前置 raise 早于 DB 写入

策略:
- 走 ``client`` fixture (内存 Redis + ASGITransport + InMemorySMS), 不打外部 IO
- mock ``llm_client.astream_chat_with_meta`` (用 fixture 文件 ``test_chat_diagnose.py``
  里同款 ``fake_streaming_llm`` / ``llm_credential_envs``, 这里再写一份独立 fixture
  避免跨文件 import)
- monkeypatch ``app.services.agent.quota.get_settings`` 让 quota 上限可控,
  无需改 .env 或 lru_cache (与 ``test_agent_quota.py`` 同手法)
- 登录用户走 ``create_access_token + Authorization: Bearer`` (与 ``test_e2e_ipo_diagnose``
  e2e 一致)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import llm_client
from app.adapters.llm_client import ChatStreamChunk, TokenUsage
from app.core.config import Settings, get_settings
from app.db.models.chat import ChatMessage, ChatSession
from app.db.models.user import User
from app.security.jwt import create_access_token

# ─── 可编程 LLM stream mock (与 test_chat_diagnose 同手法, 复制以保持文件自洽) ──


@dataclass
class _StreamScript:
    deltas: list[str] = field(default_factory=list)
    finish_reason: str = "stop"
    tool_calls: list[dict[str, Any]] | None = None
    usage_prompt: int = 50
    usage_completion: int = 80


@pytest.fixture
async def fake_streaming_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Callable[[_StreamScript], None]]:
    queue: list[_StreamScript] = []

    def push(script: _StreamScript) -> None:
        queue.append(script)

    async def fake_astream(
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int = 1500,
    ) -> AsyncIterator[ChatStreamChunk]:
        if not queue:
            raise RuntimeError(
                "fake_streaming_llm: 测试未提供脚本但主循环又调了一次 LLM"
            )
        script = queue.pop(0)
        for d in script.deltas:
            yield ChatStreamChunk(delta=d)
        yield ChatStreamChunk(
            finish_reason=script.finish_reason,
            usage=TokenUsage(
                prompt_tokens=script.usage_prompt,
                completion_tokens=script.usage_completion,
                total_tokens=script.usage_prompt + script.usage_completion,
                cost_cny=Decimal("0.001"),
            ),
            tool_calls=script.tool_calls,
        )

    monkeypatch.setattr(llm_client, "astream_chat_with_meta", fake_astream)
    yield push


@pytest.fixture
async def llm_credential_envs(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    s = get_settings()
    monkeypatch.setattr(s, "siliconflow_api_key", "test-fake-key")
    yield


# ─── quota settings override ───────────────────────────────────────────


@pytest.fixture
def override_quota_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., Settings]:
    """覆盖 ``services.agent.quota`` 模块用到的 settings.

    quota.py 在每次调用时 ``get_settings()``, 所以 monkeypatch 就只换 quota
    模块的引用即可, 不用动 lru_cache.
    """
    base = get_settings()

    def _override(
        *,
        free_per_window: int | None = None,
        anon_per_window: int | None = None,
        vip_per_window: int | None = None,
        window_seconds: int | None = None,
        vip_user_id_whitelist: str | None = None,
    ) -> Settings:
        new = base.model_copy(
            update={
                k: v
                for k, v in {
                    "agent_quota_free_per_window": free_per_window,
                    "agent_quota_anonymous_per_window": anon_per_window,
                    "agent_quota_vip_per_window": vip_per_window,
                    "agent_quota_window_seconds": window_seconds,
                    "vip_user_id_whitelist": vip_user_id_whitelist,
                }.items()
                if v is not None
            }
        )
        monkeypatch.setattr("app.services.agent.quota.get_settings", lambda: new)
        return new

    return _override


# ─── 工具: SSE 解析 + 用户/Token helper ─────────────────────────────────


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """与 test_chat_diagnose 同款 SSE 解析."""
    events: list[tuple[str, dict[str, Any]]] = []
    normalized = raw.replace("\r\n", "\n")
    blocks = [b for b in normalized.split("\n\n") if b.strip()]
    for blk in blocks:
        event_type: str | None = None
        data_lines: list[str] = []
        for line in blk.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if event_type is None or not data_lines:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            payload = {"_raw": "\n".join(data_lines)}
        events.append((event_type, payload))
    return events


async def _seed_user_and_token(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    phone_suffix: str = "0001",
) -> tuple[uuid.UUID, str]:
    """创建一条 User + 拼一个 access_token, 返回 (user_id, bearer_token).

    User 字段对齐 ``users`` 表 NOT NULL: phone (唯一) + invite_code (唯一);
    剩余字段走默认值 (region=CN, status=1, last_active_at server_default now()).
    """
    async with session_factory() as s:
        u = User(
            phone=f"+8613800{phone_suffix}",
            invite_code=f"TST{phone_suffix}",
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        uid = u.user_id
    token, _ = create_access_token(user_id=uid)
    return uid, token


# ─── 测试 1: 匿名超额 → 429 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_diagnose_anonymous_quota_exceeds_429(
    client: httpx.AsyncClient,
    fake_streaming_llm: Callable[[_StreamScript], None],
    llm_credential_envs: None,  # noqa: ARG001
    override_quota_settings: Callable[..., Settings],
) -> None:
    """匿名 anon_per_window=1: 第 1 次 200 SSE, 第 2 次 429 + ChatQuotaExceededResponse."""
    override_quota_settings(anon_per_window=1, window_seconds=86400)
    push = fake_streaming_llm
    push(_StreamScript(deltas=["第一次回答\n"], finish_reason="stop"))

    # 第 1 次: 通过, SSE 走完
    r1 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "诊断一下吧"},
    )
    assert r1.status_code == 200, r1.text
    events = _parse_sse(r1.text)
    assert events[0][0] == "start"
    assert events[-1][0] == "end"

    # 第 2 次: 不应再调 LLM (没 push 第二个 script), 入口前置 429
    r2 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "再来一次"},
    )
    assert r2.status_code == 429, r2.text
    body = r2.json()
    detail = body["detail"]
    assert detail["code"] == "agent_quota_exceeded"
    assert "今日 Agent 调用次数已用完" in detail["message"]
    quota = detail["quota"]
    assert quota["plan"] == "anonymous"
    assert quota["limit"] == 1
    assert quota["used"] == 1
    assert quota["remaining"] == 0
    assert quota["window_seconds"] == 86400
    assert quota["retry_after_seconds"] is not None
    assert quota["retry_after_seconds"] > 0
    # Retry-After header 也应被设置
    assert r2.headers.get("retry-after") == str(quota["retry_after_seconds"])


# ─── 测试 2: 超额时 user_message 不应再落 chat_messages ────────────────


@pytest.mark.asyncio
async def test_chat_diagnose_429_does_not_persist_user_message(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_streaming_llm: Callable[[_StreamScript], None],
    llm_credential_envs: None,  # noqa: ARG001
    override_quota_settings: Callable[..., Settings],
) -> None:
    """配额闸门在 SSE 流之前 (HTTPException), user_message 不会被写入 DB.

    第 1 次写 1 条 user + 1 条 assistant; 第 2 次 429 不写任何 message.
    """
    override_quota_settings(anon_per_window=1, window_seconds=86400)
    push = fake_streaming_llm
    push(_StreamScript(deltas=["回答\n"], finish_reason="stop"))

    r1 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "第一次提问"},
    )
    assert r1.status_code == 200

    r2 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "第二次提问 - 应该被挡"},
    )
    assert r2.status_code == 429

    # DB: 只应有第 1 次的 1 条 user + 1 条 assistant; 第 2 次的 user 没落
    async with session_factory() as s:
        msgs = (
            (await s.execute(select(ChatMessage).order_by(ChatMessage.created_at)))
            .scalars()
            .all()
        )
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "第一次提问"
        assert msgs[1].role == "assistant"
        # 没有"第二次提问 - 应该被挡"这条
        assert all("第二次提问" not in m.content for m in msgs)


# ─── 测试 3: 登录 FREE 用户超额 → 429 + plan=free ──────────────────────


@pytest.mark.asyncio
async def test_chat_diagnose_free_user_quota_exceeds_429(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_streaming_llm: Callable[[_StreamScript], None],
    llm_credential_envs: None,  # noqa: ARG001
    override_quota_settings: Callable[..., Settings],
) -> None:
    """FREE 用户 free_per_window=1: 第 1 次 200, 第 2 次 429 plan=free."""
    override_quota_settings(free_per_window=1, window_seconds=86400)
    _, token = await _seed_user_and_token(session_factory, phone_suffix="0010")
    h = {"Authorization": f"Bearer {token}"}

    push = fake_streaming_llm
    push(_StreamScript(deltas=["FREE 第一次\n"], finish_reason="stop"))

    r1 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "FREE 用户第一问"},
        headers=h,
    )
    assert r1.status_code == 200, r1.text

    r2 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "FREE 用户第二问"},
        headers=h,
    )
    assert r2.status_code == 429, r2.text
    detail = r2.json()["detail"]
    assert detail["code"] == "agent_quota_exceeded"
    assert detail["quota"]["plan"] == "free"
    assert detail["quota"]["limit"] == 1
    assert "升级 VIP" in detail["message"]


# ─── 测试 4: 不同用户互不影响 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_diagnose_quota_isolated_per_user(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_streaming_llm: Callable[[_StreamScript], None],
    llm_credential_envs: None,  # noqa: ARG001
    override_quota_settings: Callable[..., Settings],
) -> None:
    """A 用户用满后 429, B 用户仍可调 (Redis key 含 user_id 隔离)."""
    override_quota_settings(free_per_window=1)
    _, token_a = await _seed_user_and_token(session_factory, phone_suffix="0020")
    _, token_b = await _seed_user_and_token(session_factory, phone_suffix="0021")

    push = fake_streaming_llm
    # A 用一次 + B 用一次, 两个 LLM script
    push(_StreamScript(deltas=["A 答案\n"], finish_reason="stop"))
    push(_StreamScript(deltas=["B 答案\n"], finish_reason="stop"))

    r_a1 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "A 第一次"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r_a1.status_code == 200

    r_a2 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "A 第二次"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r_a2.status_code == 429

    # B 仍可调 (LLM script 还有一份, 不会 RuntimeError)
    r_b1 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "B 第一次"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r_b1.status_code == 200, r_b1.text


# ─── 测试 5: VIP 不限流 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_diagnose_vip_unlimited(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_streaming_llm: Callable[[_StreamScript], None],
    llm_credential_envs: None,  # noqa: ARG001
    override_quota_settings: Callable[..., Settings],
) -> None:
    """VIP user_id 在 whitelist, free_per_window=1 也连调 3 次都 200."""
    user_id, token = await _seed_user_and_token(session_factory, phone_suffix="0030")
    override_quota_settings(
        free_per_window=1,  # 故意紧, 验证 VIP 不被这个限
        vip_per_window=-1,
        vip_user_id_whitelist=str(user_id),
    )
    h = {"Authorization": f"Bearer {token}"}

    push = fake_streaming_llm
    for i in range(3):
        push(_StreamScript(deltas=[f"VIP 答案 {i}\n"], finish_reason="stop"))

    for i in range(3):
        r = await client.post(
            "/api/v1/chat/diagnose",
            json={"question": f"VIP 第 {i + 1} 问"},
            headers=h,
        )
        assert r.status_code == 200, f"VIP 第 {i + 1} 问被错挡: {r.text}"

    # DB: 应该有 3 条 user + 3 条 assistant
    async with session_factory() as s:
        msg_count = (
            await s.execute(select(ChatMessage))
        ).scalars().all()
        assert len(msg_count) == 6  # 3 user + 3 assistant
        sessions = (await s.execute(select(ChatSession))).scalars().all()
        # 没传 session_id, 每次开新 session
        assert len(sessions) == 3
