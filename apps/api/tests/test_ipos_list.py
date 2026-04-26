"""BE-008: ``GET /api/v1/ipos`` 切回 DB + 筛选 + 分页 + Redis 缓存 测试.

覆盖:

A. HK (走 seed, 不需要 DB; ``redis_client`` fixture 用 InMemoryRedisClient):
   - 默认: 返回 seed 全部
   - status=listed: 仅 listed
   - industry 精确匹配
   - 分页: total 不变, items 切片正确

B. A 股 (DB, ``@pytest.mark.db``):
   - 灌 5 条 seed → 默认: 全部按 listing_date DESC NULLS LAST 返回
   - status / industry 筛选
   - 分页: page=2,size=2 拿到第 3-4 条
   - 排序: NULL listing_date 排到最末

C. 缓存命中:
   - 第一次 query → 打 DB
   - 第二次 同参数 → 不打 DB (monkey-patch DB factory 检测调用次数)
   - 不同参数 → 重新打 DB (cache key 含参数 hash)

D. ``GET /ipos/{code}`` (顺手验证 BE-008 没破坏旧路径):
   - A 股 code 命中 → 200
   - HK 股 code 命中 seed → 200
   - 不存在 → 404

E. 入参校验:
   - market=US → 200 + items=[] (Sprint 3+ 占位)
   - size > 100 → 422
   - page < 1 → 422
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters import akshare_client
from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.db.base import get_engine
from app.db.base import get_session_factory as _get_factory_lru
from app.main import create_app
from app.schemas.ipo import IPOItem
from app.services import ipo_ingest_service, ipo_service


# ───────────────── 共享 fixture ─────────────────


@pytest.fixture
async def redis_client() -> AsyncIterator[InMemoryRedisClient]:
    client = InMemoryRedisClient()
    set_redis_client(client)
    yield client
    await client.aclose()
    reset_redis_client()


@pytest.fixture
async def app_client(
    redis_client: InMemoryRedisClient,  # noqa: ARG001
) -> AsyncIterator[httpx.AsyncClient]:
    """无 DB 依赖的 client (HK seed / 入参校验 用)."""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ───────────────── A. HK seed (no DB) ─────────────────


async def test_list_hk_default_returns_all_seed(
    app_client: httpx.AsyncClient,
) -> None:
    r = await app_client.get("/api/v1/ipos?market=HK")
    assert r.status_code == 200
    body = r.json()
    assert body["market"] == "HK"
    assert body["page"] == 1
    assert body["size"] == 20
    # seed 当前 3 条
    assert body["total"] == 3
    assert len(body["items"]) == 3
    codes = {it["code"] for it in body["items"]}
    assert codes == {"09660.HK", "06677.HK", "02015.HK"}


async def test_list_hk_filter_status(app_client: httpx.AsyncClient) -> None:
    r = await app_client.get("/api/v1/ipos?market=HK&status=listed")
    assert r.status_code == 200
    body = r.json()
    assert all(it["status"] == "listed" for it in body["items"])
    assert body["total"] == len(body["items"])


async def test_list_hk_filter_industry_exact_match(
    app_client: httpx.AsyncClient,
) -> None:
    r = await app_client.get(
        "/api/v1/ipos", params={"market": "HK", "industry": "新能源车"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["code"] == "02015.HK"


async def test_list_hk_pagination(app_client: httpx.AsyncClient) -> None:
    r = await app_client.get("/api/v1/ipos?market=HK&page=1&size=2")
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2

    r = await app_client.get("/api/v1/ipos?market=HK&page=2&size=2")
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1


async def test_list_us_returns_empty_placeholder(
    app_client: httpx.AsyncClient,
) -> None:
    r = await app_client.get("/api/v1/ipos?market=US")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "items": [],
        "total": 0,
        "market": "US",
        "page": 1,
        "size": 20,
    }


# ───────────────── E. 入参校验 (no DB) ─────────────────


async def test_list_size_too_large_422(app_client: httpx.AsyncClient) -> None:
    r = await app_client.get("/api/v1/ipos?market=HK&size=101")
    assert r.status_code == 422


async def test_list_page_zero_422(app_client: httpx.AsyncClient) -> None:
    r = await app_client.get("/api/v1/ipos?market=HK&page=0")
    assert r.status_code == 422


# ───────────────── DB-backed (A 股) ─────────────────

pytestmark_db = pytest.mark.db


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
async def truncate_ipos(db_engine) -> AsyncIterator[None]:
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE ipos RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
async def patch_session_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """让 ``ipo_service`` / ``ipo_ingest_service`` 用测试库 factory."""
    _get_factory_lru.cache_clear()
    get_engine.cache_clear()

    import app.db as db_pkg
    import app.services.ipo_ingest_service as ingest_mod
    import app.services.ipo_service as svc_mod

    orig_pkg = db_pkg.get_session_factory
    orig_ingest = ingest_mod.get_session_factory
    orig_svc = svc_mod.get_session_factory
    db_pkg.get_session_factory = lambda: session_factory  # type: ignore[assignment]
    ingest_mod.get_session_factory = lambda: session_factory  # type: ignore[assignment]
    svc_mod.get_session_factory = lambda: session_factory  # type: ignore[assignment]
    try:
        yield
    finally:
        db_pkg.get_session_factory = orig_pkg
        ingest_mod.get_session_factory = orig_ingest
        svc_mod.get_session_factory = orig_svc
        _get_factory_lru.cache_clear()
        get_engine.cache_clear()


@pytest.fixture
async def db_app_client(
    redis_client: InMemoryRedisClient,  # noqa: ARG001
    truncate_ipos: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> AsyncIterator[httpx.AsyncClient]:
    """需要 DB 的 client; truncate + patch factory 都 ready."""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _seed_a(
    code: str,
    *,
    name: str,
    industry: str = "信息技术",
    listing_date: date | None = None,
    status: str = "listed",
    issue_price: str = "10.00",
) -> IPOItem:
    return IPOItem(
        code=code,
        name=name,
        market="A",
        industry=industry,
        issue_price=Decimal(issue_price),
        issue_currency="CNY",
        listing_date=listing_date,
        pe_ratio=Decimal("23.45"),
        status=status,  # type: ignore[arg-type]
        data_source="test",
        updated_at=datetime.now(UTC).replace(tzinfo=None),
    )


async def _seed(
    factory: async_sessionmaker[AsyncSession], items: list[IPOItem]
) -> None:
    async with factory() as session:
        await ipo_ingest_service.upsert_ipos(session, items)
        await session.commit()


# ─── B. A 股 默认 + 排序 ───


@pytest.mark.db
async def test_list_a_default_sorted_by_listing_date_desc_nulls_last(
    db_app_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(
        session_factory,
        [
            _seed_a("600001.SH", name="老股", listing_date=date(2024, 1, 1)),
            _seed_a("600002.SH", name="新股", listing_date=date(2025, 6, 1)),
            _seed_a("600003.SH", name="无日期", listing_date=None, status="upcoming"),
            _seed_a("600004.SH", name="中股", listing_date=date(2025, 1, 1)),
        ],
    )

    r = await db_app_client.get("/api/v1/ipos?market=A&size=10")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4
    codes = [it["code"] for it in body["items"]]
    # 期望: 600002 (2025-06-01) → 600004 (2025-01-01) → 600001 (2024-01-01) → 600003 (NULL)
    assert codes == ["600002.SH", "600004.SH", "600001.SH", "600003.SH"]


@pytest.mark.db
async def test_list_a_filter_status(
    db_app_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(
        session_factory,
        [
            _seed_a("600001.SH", name="A", status="listed"),
            _seed_a("600002.SH", name="B", status="listed"),
            _seed_a("600003.SH", name="C", status="upcoming"),
        ],
    )

    r = await db_app_client.get("/api/v1/ipos?market=A&status=upcoming")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["code"] == "600003.SH"


@pytest.mark.db
async def test_list_a_filter_industry(
    db_app_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(
        session_factory,
        [
            _seed_a("600001.SH", name="科技股", industry="信息技术"),
            _seed_a("600002.SH", name="食品股", industry="食品饮料"),
        ],
    )

    r = await db_app_client.get(
        "/api/v1/ipos", params={"market": "A", "industry": "食品饮料"}
    )
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["code"] == "600002.SH"
    assert body["items"][0]["industry"] == "食品饮料"


@pytest.mark.db
async def test_list_a_pagination(
    db_app_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    items = [
        _seed_a(
            f"60000{i}.SH",
            name=f"股{i}",
            listing_date=date(2025, 1, i),
        )
        for i in range(1, 6)
    ]
    await _seed(session_factory, items)

    r = await db_app_client.get("/api/v1/ipos?market=A&page=1&size=2")
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    page1_codes = [it["code"] for it in body["items"]]

    r = await db_app_client.get("/api/v1/ipos?market=A&page=2&size=2")
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    page2_codes = [it["code"] for it in body["items"]]

    r = await db_app_client.get("/api/v1/ipos?market=A&page=3&size=2")
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 1

    # page1/page2 不能重叠
    assert set(page1_codes).isdisjoint(set(page2_codes))


# ─── C. 缓存命中 ───


@pytest.mark.db
async def test_list_a_second_call_hits_cache(
    db_app_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(
        session_factory,
        [_seed_a("600001.SH", name="测试", listing_date=date(2025, 1, 1))],
    )

    call_count = {"n": 0}
    orig = ipo_service._list_ipos_db  # type: ignore[attr-defined]

    async def counting(*args, **kwargs):
        call_count["n"] += 1
        return await orig(*args, **kwargs)

    monkeypatch.setattr(ipo_service, "_list_ipos_db", counting)

    # 首次: 打 DB
    r1 = await db_app_client.get("/api/v1/ipos?market=A&size=5")
    assert r1.status_code == 200
    assert call_count["n"] == 1

    # 同参数: 走缓存
    r2 = await db_app_client.get("/api/v1/ipos?market=A&size=5")
    assert r2.status_code == 200
    assert call_count["n"] == 1, "同参数第二次应该命中缓存, 不再打 DB"
    assert r1.json() == r2.json()

    # 不同 size: 缓存 key 不同, 重新打 DB
    r3 = await db_app_client.get("/api/v1/ipos?market=A&size=10")
    assert r3.status_code == 200
    assert call_count["n"] == 2, "size 改变 cache key 改变, 应该重新打 DB"


# ─── D. /ipos/{code} 详情 (顺手保护回归) ───


@pytest.mark.db
async def test_get_ipo_a_hit(
    db_app_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(
        session_factory,
        [_seed_a("600001.SH", name="测试", listing_date=date(2025, 1, 1))],
    )

    r = await db_app_client.get("/api/v1/ipos/600001.SH")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "600001.SH"
    assert body["name"] == "测试"


async def test_get_ipo_hk_hit_seed(app_client: httpx.AsyncClient) -> None:
    r = await app_client.get("/api/v1/ipos/02015.HK")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "02015.HK"
    assert body["name"] == "理想汽车-W"


async def test_get_ipo_not_found_hk(app_client: httpx.AsyncClient) -> None:
    r = await app_client.get("/api/v1/ipos/99999.HK")
    assert r.status_code == 404


@pytest.mark.db
async def test_get_ipo_not_found_a(
    db_app_client: httpx.AsyncClient,
) -> None:
    r = await db_app_client.get("/api/v1/ipos/000999.SZ")
    assert r.status_code == 404


# silence unused
_ = akshare_client
