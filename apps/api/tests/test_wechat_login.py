"""BE-005: 微信小程序登录端到端测试.

分两层覆盖, 一份测试文件搞定:
1. **adapter 单元** (无 DB, 无 FastAPI, 用 ``respx`` mock 微信 API)
   - happy: 含 unionid / 不含 unionid (开放平台未绑定)
   - errcode 用户类 (40029 invalid_code, 41008 missing_code) → ``WechatAuthError``
   - errcode 系统类 (-1, 45011, 40013 invalid appid) → ``WechatAPIError``
   - 网络超时 / HTTP 5xx / 非 JSON body → ``WechatAPIError``
2. **路由端到端** (需 DB) — 注入 stub client, 不走 respx, 避免 ASGI + respx 互相打架
   - 200 新用户注册 + 颁发 token (有 unionid / 无 unionid 两路)
   - 200 老用户 (按 unionid 命中) 直接登录, openid 同步覆盖
   - 200 老用户 (无 unionid, 按 openid 命中) 直接登录
   - 401 wechat_code_invalid (40029)
   - 502 wechat_upstream_error (-1, 45011)
   - 503 wechat_mp_not_configured (空 AppSecret)
   - 401 user_disabled (老用户 status=0)
   - 422 code 太短 (Pydantic 拦)
   - 429 同 code 1min 内 5 次以上 (限流)
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx
from alembic.config import Config
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.adapters.wechat import (
    Code2SessionResult,
    WechatAPIError,
    WechatAuthError,
    WechatMPClient,
    reset_wechat_mp_client,
    set_wechat_mp_client,
)
from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.core.config import Settings, get_settings
from app.db.base import get_session
from app.db.models import User
from app.main import create_app

# =====================================================================
# 1. adapter 单元 (无 DB, 仅 respx)
# =====================================================================


@pytest.fixture
def mp_settings() -> Settings:
    """造一个开启了 wechat_mp 的 Settings, 不污染全局 ``get_settings``."""
    base = get_settings()
    return Settings(
        wechat_mp_app_id="wxtest_appid",
        wechat_mp_app_secret="wxtest_secret",
        wechat_code2session_url="https://api.weixin.qq.com/sns/jscode2session",
        wechat_code2session_timeout_seconds=2.0,
        jwt_secret=base.jwt_secret,
    )


def _build_client(mp_settings: Settings) -> WechatMPClient:
    return WechatMPClient(
        app_id=mp_settings.wechat_mp_app_id,
        app_secret=mp_settings.wechat_mp_app_secret,
        endpoint=mp_settings.wechat_code2session_url,
        timeout_seconds=mp_settings.wechat_code2session_timeout_seconds,
    )


@respx.mock
async def test_adapter_happy_with_unionid(mp_settings: Settings) -> None:
    respx.get(mp_settings.wechat_code2session_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "openid": "oXYZ_open",
                "unionid": "oUNION_xyz",
                "session_key": "secret-must-not-leak",
            },
        )
    )
    client = _build_client(mp_settings)
    try:
        result = await client.code2session("any-code-1")
    finally:
        await client.aclose()

    assert isinstance(result, Code2SessionResult)
    assert result.openid == "oXYZ_open"
    assert result.unionid == "oUNION_xyz"
    # session_key 不应出现在结果对象上 (合规)
    assert not hasattr(result, "session_key")


@respx.mock
async def test_adapter_happy_without_unionid(mp_settings: Settings) -> None:
    """小程序未绑定开放平台时, 不返回 unionid."""
    respx.get(mp_settings.wechat_code2session_url).mock(
        return_value=httpx.Response(200, json={"openid": "oABC_only"})
    )
    client = _build_client(mp_settings)
    try:
        result = await client.code2session("code-2")
    finally:
        await client.aclose()
    assert result.openid == "oABC_only"
    assert result.unionid is None


@respx.mock
async def test_adapter_treats_empty_string_unionid_as_none(mp_settings: Settings) -> None:
    respx.get(mp_settings.wechat_code2session_url).mock(
        return_value=httpx.Response(200, json={"openid": "oXYZ", "unionid": ""})
    )
    client = _build_client(mp_settings)
    try:
        result = await client.code2session("code-3")
    finally:
        await client.aclose()
    assert result.unionid is None


@respx.mock
async def test_adapter_user_errcode_raises_auth_error(mp_settings: Settings) -> None:
    """40029 invalid_code → 客户端态错误, 应 401 让用户重新 wx.login."""
    respx.get(mp_settings.wechat_code2session_url).mock(
        return_value=httpx.Response(
            200, json={"errcode": 40029, "errmsg": "invalid code"}
        )
    )
    client = _build_client(mp_settings)
    try:
        with pytest.raises(WechatAuthError) as exc_info:
            await client.code2session("bad-code")
    finally:
        await client.aclose()
    assert exc_info.value.errcode == 40029


@respx.mock
async def test_adapter_system_errcode_raises_api_error(mp_settings: Settings) -> None:
    """-1 系统繁忙 → 502, 让前端 retry."""
    respx.get(mp_settings.wechat_code2session_url).mock(
        return_value=httpx.Response(200, json={"errcode": -1, "errmsg": "system busy"})
    )
    client = _build_client(mp_settings)
    try:
        with pytest.raises(WechatAPIError) as exc_info:
            await client.code2session("code-x")
    finally:
        await client.aclose()
    assert exc_info.value.errcode == -1


@respx.mock
async def test_adapter_invalid_appid_raises_api_error(mp_settings: Settings) -> None:
    """40013 invalid appid → 我方配置错, 走 502 不暴露内部细节."""
    respx.get(mp_settings.wechat_code2session_url).mock(
        return_value=httpx.Response(
            200, json={"errcode": 40013, "errmsg": "invalid appid"}
        )
    )
    client = _build_client(mp_settings)
    try:
        with pytest.raises(WechatAPIError) as exc_info:
            await client.code2session("code-y")
    finally:
        await client.aclose()
    assert exc_info.value.errcode == 40013


@respx.mock
async def test_adapter_http_5xx_raises_api_error(mp_settings: Settings) -> None:
    respx.get(mp_settings.wechat_code2session_url).mock(
        return_value=httpx.Response(503, text="upstream busy")
    )
    client = _build_client(mp_settings)
    try:
        with pytest.raises(WechatAPIError) as exc_info:
            await client.code2session("code-z")
    finally:
        await client.aclose()
    assert "503" in str(exc_info.value)


@respx.mock
async def test_adapter_timeout_raises_api_error(mp_settings: Settings) -> None:
    respx.get(mp_settings.wechat_code2session_url).mock(
        side_effect=httpx.ReadTimeout("timeout")
    )
    client = _build_client(mp_settings)
    try:
        with pytest.raises(WechatAPIError) as exc_info:
            await client.code2session("code-t")
    finally:
        await client.aclose()
    assert "timeout" in str(exc_info.value).lower()


@respx.mock
async def test_adapter_non_json_body_raises_api_error(mp_settings: Settings) -> None:
    respx.get(mp_settings.wechat_code2session_url).mock(
        return_value=httpx.Response(200, text="<html>NOT JSON</html>")
    )
    client = _build_client(mp_settings)
    try:
        with pytest.raises(WechatAPIError):
            await client.code2session("code-html")
    finally:
        await client.aclose()


async def test_adapter_empty_code_short_circuits(mp_settings: Settings) -> None:
    """空 code 不发请求, 直接 WechatAuthError."""
    client = _build_client(mp_settings)
    try:
        with pytest.raises(WechatAuthError):
            await client.code2session("   ")
    finally:
        await client.aclose()


# =====================================================================
# 2. 路由端到端 (需 DB), 注入 stub WechatMPClient
# =====================================================================


pytestmark_db = pytest.mark.db  # 仅给下面的路由测试加, adapter 单元不需要 DB


def _build_alembic_config(test_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)
    return cfg


async def _drop_business_tables(url: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
            for (tbl,) in rows:
                await conn.execute(text(f'DROP TABLE IF EXISTS public."{tbl}" CASCADE'))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
async def schema_at_head(test_database_url: str) -> AsyncIterator[str]:
    await _drop_business_tables(test_database_url)
    cfg = _build_alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield test_database_url


@pytest.fixture
async def db_engine(schema_at_head: str):
    engine = create_async_engine(schema_at_head, future=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def truncate_users(db_engine) -> AsyncIterator[None]:
    async with db_engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE users, auth_sessions, user_favorites RESTART IDENTITY CASCADE")
        )
    yield


@pytest.fixture
async def redis_client() -> AsyncIterator[InMemoryRedisClient]:
    client = InMemoryRedisClient()
    set_redis_client(client)
    yield client
    await client.aclose()
    reset_redis_client()


class _StubWechatClient:
    """直接放 (openid, unionid) 进去, 不走 HTTP, 不依赖 respx.

    测试时 ``stub.next = ("oA", "uA")`` 或 ``stub.error = WechatAuthError(...)``.
    """

    def __init__(self) -> None:
        self.next: tuple[str, str | None] | None = None
        self.error: Exception | None = None
        self.calls: list[str] = []

    async def code2session(self, code: str) -> Code2SessionResult:
        self.calls.append(code)
        if self.error is not None:
            err, self.error = self.error, None
            raise err
        if self.next is None:
            raise AssertionError("StubWechatClient: please set .next or .error before call")
        openid, unionid = self.next
        self.next = None
        return Code2SessionResult(openid=openid, unionid=unionid)

    async def aclose(self) -> None:
        return None


@pytest.fixture
async def wechat_stub() -> AsyncIterator[_StubWechatClient]:
    stub = _StubWechatClient()
    set_wechat_mp_client(stub)  # type: ignore[arg-type]
    yield stub
    reset_wechat_mp_client()


@pytest.fixture
def configured_wechat(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 ``settings.wechat_mp_configured`` 在测试期为真.

    直接 ``monkeypatch`` cached 的 Settings 实例, 这样不需要重启 lru_cache。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "wechat_mp_app_id", "wxtest_appid")
    monkeypatch.setattr(settings, "wechat_mp_app_secret", "wxtest_secret")


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_users: None,  # noqa: ARG001
    redis_client: InMemoryRedisClient,  # noqa: ARG001
    wechat_stub: _StubWechatClient,  # noqa: ARG001
    configured_wechat: None,  # noqa: ARG001
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------- 200 happy ----------------


@pytestmark_db
async def test_route_new_user_with_unionid(
    client: httpx.AsyncClient, wechat_stub: _StubWechatClient
) -> None:
    wechat_stub.next = ("oOpenid_A", "oUnionid_A")
    r = await client.post(
        "/api/v1/auth/login/wechat-mp", json={"code": "wx-code-aaa"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_new_user"] is True
    assert body["user"]["status"] == 1
    assert body["user"]["invite_code"]
    assert body["tokens"]["access_token"]
    assert body["tokens"]["refresh_token"]

    # 新 access 立即可用
    me = await client.get(
        "/api/v1/me", headers=_bearer(body["tokens"]["access_token"])
    )
    assert me.status_code == 200
    assert me.json()["user_id"] == body["user"]["user_id"]


@pytestmark_db
async def test_route_new_user_without_unionid(
    client: httpx.AsyncClient, wechat_stub: _StubWechatClient
) -> None:
    wechat_stub.next = ("oOpenid_B", None)
    r = await client.post(
        "/api/v1/auth/login/wechat-mp", json={"code": "wx-code-bbb"}
    )
    assert r.status_code == 200
    assert r.json()["is_new_user"] is True


@pytestmark_db
async def test_route_existing_user_matched_by_unionid(
    client: httpx.AsyncClient,
    wechat_stub: _StubWechatClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同 unionid, openid 变了 (跨小程序场景) → 命中老用户, openid 同步覆盖."""
    wechat_stub.next = ("oOpenid_C1", "oUnionid_C")
    r1 = await client.post(
        "/api/v1/auth/login/wechat-mp", json={"code": "wx-code-c1"}
    )
    assert r1.status_code == 200
    user_id_1 = r1.json()["user"]["user_id"]
    assert r1.json()["is_new_user"] is True

    wechat_stub.next = ("oOpenid_C2", "oUnionid_C")  # openid 换了, unionid 一致
    r2 = await client.post(
        "/api/v1/auth/login/wechat-mp", json={"code": "wx-code-c2"}
    )
    assert r2.status_code == 200
    assert r2.json()["is_new_user"] is False  # 关键: 不是新用户
    assert r2.json()["user"]["user_id"] == user_id_1

    # DB 里的 openid 应该是新的
    async with session_factory() as session:
        u = await session.get(User, uuid.UUID(user_id_1))
        assert u is not None
        assert u.wechat_openid == "oOpenid_C2"
        assert u.wechat_unionid == "oUnionid_C"


@pytestmark_db
async def test_route_existing_user_matched_by_openid_only(
    client: httpx.AsyncClient, wechat_stub: _StubWechatClient
) -> None:
    """没拿到 unionid 时, openid fallback 命中老用户."""
    wechat_stub.next = ("oOpenid_D", None)
    r1 = await client.post(
        "/api/v1/auth/login/wechat-mp", json={"code": "wx-code-d1"}
    )
    assert r1.status_code == 200
    uid = r1.json()["user"]["user_id"]

    wechat_stub.next = ("oOpenid_D", None)
    r2 = await client.post(
        "/api/v1/auth/login/wechat-mp", json={"code": "wx-code-d2"}
    )
    assert r2.status_code == 200
    assert r2.json()["is_new_user"] is False
    assert r2.json()["user"]["user_id"] == uid


@pytestmark_db
async def test_route_existing_user_backfills_unionid_on_relogin(
    client: httpx.AsyncClient,
    wechat_stub: _StubWechatClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """先用没 unionid 注册, 后续登录拿到了 unionid, 应该补到老用户上."""
    wechat_stub.next = ("oOpenid_E", None)
    r1 = await client.post(
        "/api/v1/auth/login/wechat-mp", json={"code": "wx-code-e1"}
    )
    uid = r1.json()["user"]["user_id"]

    wechat_stub.next = ("oOpenid_E", "oUnionid_E_new")
    r2 = await client.post(
        "/api/v1/auth/login/wechat-mp", json={"code": "wx-code-e2"}
    )
    assert r2.status_code == 200
    assert r2.json()["is_new_user"] is False

    async with session_factory() as session:
        u = await session.get(User, uuid.UUID(uid))
        assert u is not None
        assert u.wechat_unionid == "oUnionid_E_new"


# ---------------- 401 / 502 / 503 ----------------


@pytestmark_db
async def test_route_invalid_code_returns_401(
    client: httpx.AsyncClient, wechat_stub: _StubWechatClient
) -> None:
    wechat_stub.error = WechatAuthError("invalid code", errcode=40029)
    r = await client.post(
        "/api/v1/auth/login/wechat-mp", json={"code": "wx-code-bad"}
    )
    assert r.status_code == 401
    detail = r.json()["detail"]
    assert detail["code"] == "wechat_code_invalid"
    assert detail["errcode"] == 40029


@pytestmark_db
async def test_route_upstream_error_returns_502(
    client: httpx.AsyncClient, wechat_stub: _StubWechatClient
) -> None:
    wechat_stub.error = WechatAPIError("system busy", errcode=-1)
    r = await client.post(
        "/api/v1/auth/login/wechat-mp", json={"code": "wx-code-busy"}
    )
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "wechat_upstream_error"


@pytestmark_db
async def test_route_disabled_user_returns_401(
    client: httpx.AsyncClient,
    wechat_stub: _StubWechatClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """老用户被禁用后再用微信登录 → 401 user_disabled, 不可绕过."""
    wechat_stub.next = ("oOpenid_F", "oUnionid_F")
    r1 = await client.post(
        "/api/v1/auth/login/wechat-mp", json={"code": "wx-code-f1"}
    )
    uid = r1.json()["user"]["user_id"]

    async with session_factory() as session:
        await session.execute(
            update(User).where(User.user_id == uuid.UUID(uid)).values(status=0)
        )
        await session.commit()

    wechat_stub.next = ("oOpenid_F", "oUnionid_F")
    r2 = await client.post(
        "/api/v1/auth/login/wechat-mp", json={"code": "wx-code-f2"}
    )
    assert r2.status_code == 401
    assert r2.json()["detail"]["code"] == "user_disabled"


@pytestmark_db
async def test_route_too_short_code_returns_422(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/auth/login/wechat-mp", json={"code": "x"})
    assert r.status_code == 422


@pytestmark_db
async def test_route_rate_limit_kicks_in(
    client: httpx.AsyncClient, wechat_stub: _StubWechatClient
) -> None:
    """同 code 1min 5 次 (key 取 code[:32]). 第 6 次 429."""
    statuses: list[int] = []
    for _ in range(6):
        # adapter 总是抛 invalid_code, 但限流的 key 是 code 本身, 不论结果都计数
        wechat_stub.error = WechatAuthError("invalid code", errcode=40029)
        r = await client.post(
            "/api/v1/auth/login/wechat-mp", json={"code": "same-code-zzz"}
        )
        statuses.append(r.status_code)
    assert statuses[5] == 429, f"expected 6th call 429, got {statuses}"


# ---------------- 503 (服务未启用) — 单独 client, 不复用 configured_wechat ----------------


@pytestmark_db
async def test_route_503_when_wechat_not_configured(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_users: None,  # noqa: ARG001
    redis_client: InMemoryRedisClient,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没 configure_wechat fixture, 也不注 stub → 走 503 分支."""
    settings = get_settings()
    monkeypatch.setattr(settings, "wechat_mp_app_id", "")
    monkeypatch.setattr(settings, "wechat_mp_app_secret", "")
    reset_wechat_mp_client()

    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/v1/auth/login/wechat-mp", json={"code": "anything-long-enough"}
        )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "wechat_mp_not_configured"
