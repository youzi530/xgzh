"""BE-003: GET /api/v1/me + ``get_current_user`` / ``get_optional_user`` 端到端测试.

覆盖:
- 200: 合法 access_token → 返回 UserPublic, sub 与登录返回的 user_id 一致
- 401 token_missing: 缺 Authorization header
- 401 token_scheme_invalid: ``Authorization: Basic xxx`` / 其它非 Bearer scheme
- 401 token_invalid: 签名错 / 篡改 / 缺 claim
- 401 token_expired: 已过期 (用 ttl=-10 自造一个)
- 401 token_invalid (typ): 把 refresh_token 当 access 用
- 401 user_not_found: token 合法但 user 已被删
- 401 user_disabled: status=0 (禁用) 或 status=-1 (封号)
- get_optional_user: 没 token → None; 错 token → None (不抛 401)

依赖真 PG (XGZH_TEST_DATABASE_URL), 标 ``@pytest.mark.db``。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import APIRouter, Depends
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.sms import (
    MockSMSAdapter,
    reset_sms_adapter,
    set_sms_adapter,
)
from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.db.base import get_session
from app.db.models import User
from app.main import create_app
from app.security import (
    create_access_token,
    create_refresh_token,
    get_optional_user,
)
from app.services import otp_service

pytestmark = pytest.mark.db


# ------------------- DB / fixture (与 test_auth_login 同源) -------------------


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


@pytest.fixture
async def mock_sms() -> AsyncIterator[MockSMSAdapter]:
    adapter = MockSMSAdapter()
    set_sms_adapter(adapter)
    yield adapter
    reset_sms_adapter()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_users: None,  # noqa: ARG001
    redis_client: InMemoryRedisClient,  # noqa: ARG001
    mock_sms: MockSMSAdapter,  # noqa: ARG001
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


# ------------------- helpers -------------------


async def _login_and_get_tokens(
    client: httpx.AsyncClient, phone: str = "+8613800138000", code: str = "123456"
) -> dict:
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    r = await client.post(
        "/api/v1/auth/login/phone", json={"phone": phone, "code": code}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ------------------- 200 happy -------------------


async def test_me_with_valid_token_returns_user(client: httpx.AsyncClient) -> None:
    body = await _login_and_get_tokens(client)
    access = body["tokens"]["access_token"]

    r = await client.get("/api/v1/me", headers=_bearer(access))
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["user_id"] == body["user"]["user_id"]
    assert me["invite_code"] == body["user"]["invite_code"]
    assert me["status"] == 1
    assert me["region"] == "CN"
    assert "phone" not in me  # 敏感字段不能泄漏


# ------------------- 401 各路径 -------------------


async def test_me_without_header_returns_401_token_missing(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_missing"
    assert r.headers.get("WWW-Authenticate", "").lower().startswith("bearer")


async def test_me_with_basic_scheme_returns_401_scheme_invalid(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get(
        "/api/v1/me", headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_scheme_invalid"


async def test_me_with_garbage_token_returns_401_invalid(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/api/v1/me", headers=_bearer("not-a-jwt"))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_invalid"


async def test_me_with_tampered_token_returns_401_invalid(
    client: httpx.AsyncClient,
) -> None:
    body = await _login_and_get_tokens(client)
    access = body["tokens"]["access_token"]
    bad = access[:-1] + ("0" if access[-1] != "0" else "1")
    r = await client.get("/api/v1/me", headers=_bearer(bad))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_invalid"


async def test_me_with_expired_token_returns_401_expired(
    client: httpx.AsyncClient,
) -> None:
    body = await _login_and_get_tokens(client)

    from app.core.config import Settings, get_settings

    base = get_settings()
    expired_settings = Settings(
        jwt_secret=base.jwt_secret,
        jwt_algorithm=base.jwt_algorithm,
        jwt_issuer=base.jwt_issuer,
        jwt_audience=base.jwt_audience,
        jwt_access_ttl_seconds=-10,
    )
    import uuid

    expired, _ = create_access_token(
        uuid.UUID(body["user"]["user_id"]), settings=expired_settings
    )
    r = await client.get("/api/v1/me", headers=_bearer(expired))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_expired"


async def test_me_with_refresh_token_returns_401_invalid_typ(
    client: httpx.AsyncClient,
) -> None:
    """refresh token 不能当 access 用; deps 应按 typ 拒绝."""
    body = await _login_and_get_tokens(client)
    refresh = body["tokens"]["refresh_token"]
    r = await client.get("/api/v1/me", headers=_bearer(refresh))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_invalid"


async def test_me_with_token_for_deleted_user_returns_401(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """先签发 token, 再把 user 软删, 之后 token 应该 401 user_not_found."""
    body = await _login_and_get_tokens(client)
    access = body["tokens"]["access_token"]

    import uuid

    async with session_factory() as session:
        await session.execute(
            update(User)
            .where(User.user_id == uuid.UUID(body["user"]["user_id"]))
            .values(deleted_at=text("now()"))
        )
        await session.commit()

    r = await client.get("/api/v1/me", headers=_bearer(access))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "user_not_found"


async def test_me_with_token_for_disabled_user_returns_401(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = await _login_and_get_tokens(client)
    access = body["tokens"]["access_token"]

    import uuid

    async with session_factory() as session:
        await session.execute(
            update(User)
            .where(User.user_id == uuid.UUID(body["user"]["user_id"]))
            .values(status=0)
        )
        await session.commit()

    r = await client.get("/api/v1/me", headers=_bearer(access))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "user_disabled"


async def test_me_with_token_for_nonexistent_uuid_returns_401(
    client: httpx.AsyncClient,
) -> None:
    """token 合法签发但 sub 指向数据库里不存在的 UUID."""
    import uuid

    fake_uid = uuid.uuid4()
    token, _ = create_access_token(fake_uid)
    r = await client.get("/api/v1/me", headers=_bearer(token))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "user_not_found"


# ------------------- get_optional_user -------------------


def _add_optional_probe(app) -> None:
    """临时挂一个 ``/__probe/optional`` 路由用于直接观测 ``get_optional_user``."""
    probe = APIRouter()

    @probe.get("/__probe/optional")
    async def _probe(user=Depends(get_optional_user)):
        if user is None:
            return {"authenticated": False, "user_id": None}
        return {"authenticated": True, "user_id": str(user.user_id)}

    app.include_router(probe)


@pytest.fixture
async def client_with_probe(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_users: None,  # noqa: ARG001
    redis_client: InMemoryRedisClient,  # noqa: ARG001
    mock_sms: MockSMSAdapter,  # noqa: ARG001
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
    _add_optional_probe(app)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_optional_user_returns_none_when_no_header(
    client_with_probe: httpx.AsyncClient,
) -> None:
    r = await client_with_probe.get("/__probe/optional")
    assert r.status_code == 200
    body = r.json()
    assert body == {"authenticated": False, "user_id": None}


async def test_optional_user_returns_none_for_invalid_token(
    client_with_probe: httpx.AsyncClient,
) -> None:
    r = await client_with_probe.get(
        "/__probe/optional", headers=_bearer("garbage.jwt.token")
    )
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is False


async def test_optional_user_returns_user_for_valid_token(
    client_with_probe: httpx.AsyncClient,
) -> None:
    body = await _login_and_get_tokens(client_with_probe)
    access = body["tokens"]["access_token"]

    r = await client_with_probe.get("/__probe/optional", headers=_bearer(access))
    assert r.status_code == 200
    out = r.json()
    assert out["authenticated"] is True
    assert out["user_id"] == body["user"]["user_id"]


async def test_optional_user_returns_none_for_refresh_token(
    client_with_probe: httpx.AsyncClient,
) -> None:
    """refresh token 在 optional 路径下也只能解 None, 不能升级成已登录身份."""
    body = await _login_and_get_tokens(client_with_probe)
    refresh = body["tokens"]["refresh_token"]
    r = await client_with_probe.get(
        "/__probe/optional", headers=_bearer(refresh)
    )
    assert r.status_code == 200
    assert r.json()["authenticated"] is False
