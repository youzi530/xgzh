"""BE-010: 用户自选股 API 端到端测试.

覆盖:

A. 鉴权
   1. 未登录 → 401 (POST / DELETE / GET 各 1 条, 走 BE-003 deps 的 ``token_missing``)

B. ``POST /api/v1/favorites``
   1. 添加新股 → 200 + ``created=True``
   2. 同 code 重复添加 → 200 + ``created=False`` (幂等)
   3. 切换 ``notify_on_subscribe`` → ``created=False`` 但 ``notify_on_subscribe`` 更新
   4. ``code`` 没后缀 → 400 ``favorite_code_invalid``
   5. 后缀未知 (``.XX``) → 400 ``favorite_code_invalid``
   6. HK code (``ipos`` 表中没有) → 200 仍可收藏

C. ``DELETE /api/v1/favorites/{code}``
   1. 已收藏 → 200 + ``removed=True``
   2. 重复删除 → 200 + ``removed=False`` (幂等)
   3. ``code`` 不合法 → 400

D. ``GET /api/v1/favorites``
   1. 空 → ``items=[] total=0``
   2. 混合: 1 条 A 股 (DB JOIN 出 name/listing_date), 1 条 HK (字段 None)
      → 按 ``favorited_at DESC`` 排序
   3. 用户隔离: 不同用户互不可见
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.adapters.sms import MockSMSAdapter, reset_sms_adapter, set_sms_adapter
from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.db.base import get_engine, get_session
from app.db.base import get_session_factory as _get_factory_lru
from app.main import create_app
from app.schemas.ipo import IPOItem
from app.services import ipo_ingest_service, otp_service

pytestmark = pytest.mark.db


# ─── Alembic schema 准备 (与其它 db 测试同源) ─────────────────────────


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
async def truncate_all(db_engine) -> AsyncIterator[None]:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE users, auth_sessions, user_favorites, ipos, invite_codes "
                "RESTART IDENTITY CASCADE"
            )
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
async def patch_session_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """让 ``ipo_ingest_service.upsert_ipos`` 用测试 session factory."""
    _get_factory_lru.cache_clear()
    get_engine.cache_clear()

    import app.db as db_pkg
    import app.services.ipo_ingest_service as ingest_mod

    orig_pkg = db_pkg.get_session_factory
    orig_ingest = ingest_mod.get_session_factory
    db_pkg.get_session_factory = lambda: session_factory  # type: ignore[assignment]
    ingest_mod.get_session_factory = lambda: session_factory  # type: ignore[assignment]
    try:
        yield
    finally:
        db_pkg.get_session_factory = orig_pkg
        ingest_mod.get_session_factory = orig_ingest
        _get_factory_lru.cache_clear()
        get_engine.cache_clear()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    redis_client: InMemoryRedisClient,  # noqa: ARG001
    mock_sms: MockSMSAdapter,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
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


# ─── helpers ─────────────────────────────────────────


async def _login(
    cli: httpx.AsyncClient, phone: str = "+8613800138000", code: str = "123456"
) -> dict:
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    r = await cli.post("/api/v1/auth/login/phone", json={"phone": phone, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _seed_a(code: str, name: str = "测试股份") -> IPOItem:
    return IPOItem(
        code=code,
        name=name,
        market="A",
        industry="信息技术",
        issue_price=Decimal("12.34"),
        issue_currency="CNY",
        listing_date=date(2025, 1, 15),
        pe_ratio=Decimal("23.45"),
        status="listed",
        data_source="test",
        updated_at=datetime.now(UTC).replace(tzinfo=None),
    )


# ═══════════ A. 鉴权 ═══════════


async def test_post_without_token_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/favorites", json={"code": "0700.HK"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_missing"


async def test_delete_without_token_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.delete("/api/v1/favorites/0700.HK")
    assert r.status_code == 401


async def test_get_without_token_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/favorites")
    assert r.status_code == 401


# ═══════════ B. POST /favorites ═══════════


async def test_add_favorite_first_time_returns_created_true(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(session, [_seed_a("600519.SH", "贵州茅台")])
        await session.commit()

    body = await _login(client)
    access = body["tokens"]["access_token"]

    r = await client.post(
        "/api/v1/favorites",
        json={"code": "600519.SH"},
        headers=_bearer(access),
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert j["code"] == "600519.SH"
    assert j["market"] == "A"
    assert j["created"] is True
    assert j["notify_on_subscribe"] is True
    assert "favorited_at" in j


async def test_add_favorite_idempotent_second_call_created_false(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(session, [_seed_a("600519.SH")])
        await session.commit()

    body = await _login(client)
    access = body["tokens"]["access_token"]
    h = _bearer(access)

    r1 = await client.post("/api/v1/favorites", json={"code": "600519.SH"}, headers=h)
    assert r1.json()["created"] is True

    r2 = await client.post("/api/v1/favorites", json={"code": "600519.SH"}, headers=h)
    assert r2.status_code == 200
    assert r2.json()["created"] is False, "重复 add 必须幂等"
    assert r2.json()["favorited_at"] == r1.json()["favorited_at"], (
        "再次 add 不应改 favorited_at"
    )


async def test_add_favorite_toggles_notify_flag(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(session, [_seed_a("600519.SH")])
        await session.commit()

    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r1 = await client.post(
        "/api/v1/favorites",
        json={"code": "600519.SH", "notify_on_subscribe": True},
        headers=h,
    )
    assert r1.json()["notify_on_subscribe"] is True

    r2 = await client.post(
        "/api/v1/favorites",
        json={"code": "600519.SH", "notify_on_subscribe": False},
        headers=h,
    )
    assert r2.status_code == 200
    assert r2.json()["created"] is False
    assert r2.json()["notify_on_subscribe"] is False, "重复 add 应可切换 notify flag"


async def test_add_favorite_lowercase_normalized_to_upper(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(session, [_seed_a("600519.SH")])
        await session.commit()

    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r = await client.post(
        "/api/v1/favorites", json={"code": "600519.sh"}, headers=h
    )
    assert r.status_code == 200
    assert r.json()["code"] == "600519.SH"


async def test_add_favorite_hk_code_even_without_ipos_row(
    client: httpx.AsyncClient,
) -> None:
    """HK seed code (尚未入 ipos 表) 也允许收藏; list 时 LEFT JOIN 字段全 None."""
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r = await client.post("/api/v1/favorites", json={"code": "0700.HK"}, headers=h)
    assert r.status_code == 200
    assert r.json()["market"] == "HK"
    assert r.json()["created"] is True


async def test_add_favorite_without_suffix_returns_400(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r = await client.post("/api/v1/favorites", json={"code": "BABA"}, headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "favorite_code_invalid"


async def test_add_favorite_unknown_suffix_returns_400(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r = await client.post(
        "/api/v1/favorites", json={"code": "600519.XX"}, headers=h
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "favorite_code_invalid"


# ═══════════ C. DELETE /favorites/{code} ═══════════


async def test_delete_favorite_happy_then_idempotent(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(session, [_seed_a("600519.SH")])
        await session.commit()

    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    await client.post("/api/v1/favorites", json={"code": "600519.SH"}, headers=h)

    r1 = await client.delete("/api/v1/favorites/600519.SH", headers=h)
    assert r1.status_code == 200
    assert r1.json()["removed"] is True

    r2 = await client.delete("/api/v1/favorites/600519.SH", headers=h)
    assert r2.status_code == 200, "重复 delete 应该 200, 不报 404"
    assert r2.json()["removed"] is False


async def test_delete_favorite_invalid_code_returns_400(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r = await client.delete("/api/v1/favorites/BABA", headers=h)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "favorite_code_invalid"


# ═══════════ D. GET /favorites ═══════════


async def test_list_empty_returns_zero(client: httpx.AsyncClient) -> None:
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r = await client.get("/api/v1/favorites", headers=h)
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


async def test_list_mixed_a_db_and_hk_seed_in_desc_order(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(
            session, [_seed_a("600519.SH", "贵州茅台")]
        )
        await session.commit()

    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    # 先收 A 股 (DB hit), 再收 HK (DB miss); 列表应按 favorited_at DESC 排
    r1 = await client.post("/api/v1/favorites", json={"code": "600519.SH"}, headers=h)
    assert r1.status_code == 200
    # 制造时间间隔, 否则同毫秒级 DESC 退化为按 ipo_code 排
    await asyncio.sleep(0.05)
    r2 = await client.post("/api/v1/favorites", json={"code": "0700.HK"}, headers=h)
    assert r2.status_code == 200

    rl = await client.get("/api/v1/favorites", headers=h)
    assert rl.status_code == 200
    body = rl.json()
    assert body["total"] == 2
    items = body["items"]
    # HK 后收, 应排前
    assert items[0]["code"] == "0700.HK"
    assert items[0]["market"] == "HK"
    assert items[0]["name"] is None, "HK seed code 不在 ipos 表 → name 应为 None"
    assert items[0]["industry"] is None
    assert items[0]["listing_date"] is None
    assert items[0]["status"] == "unknown"

    assert items[1]["code"] == "600519.SH"
    assert items[1]["market"] == "A"
    assert items[1]["name"] == "贵州茅台", "A 股已 ingest → 应 JOIN 出 name"
    assert items[1]["industry"] == "信息技术"
    assert items[1]["listing_date"] == "2025-01-15"
    assert items[1]["status"] == "listed"
    assert items[1]["issue_price"] == 12.34


async def test_list_isolated_per_user(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(
            session, [_seed_a("600519.SH"), _seed_a("000858.SZ", "五粮液")]
        )
        await session.commit()

    # user A
    body_a = await _login(client, phone="+8613800138000")
    h_a = _bearer(body_a["tokens"]["access_token"])
    await client.post("/api/v1/favorites", json={"code": "600519.SH"}, headers=h_a)

    # user B (不同 phone → 不同 user)
    body_b = await _login(client, phone="+8613900139000")
    h_b = _bearer(body_b["tokens"]["access_token"])
    await client.post("/api/v1/favorites", json={"code": "000858.SZ"}, headers=h_b)

    list_a = (await client.get("/api/v1/favorites", headers=h_a)).json()
    list_b = (await client.get("/api/v1/favorites", headers=h_b)).json()

    a_codes = [it["code"] for it in list_a["items"]]
    b_codes = [it["code"] for it in list_b["items"]]
    assert a_codes == ["600519.SH"]
    assert b_codes == ["000858.SZ"]
