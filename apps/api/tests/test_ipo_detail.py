"""BE-009: ``GET /api/v1/ipos/{code}`` 字段聚合详情 测试.

覆盖:

A. A 股 (DB, ``@pytest.mark.db``):
   1. happy: 灌一行带完整字段 (sponsors / prospectus_url / extra.highlights /
      extra.risks / extra.financial_summary) → GET 详情, 字段全部从 ORM/JSONB
      正确还原
   2. extra 缺字段时, 详情返回空 list / None, 不报错
   3. 不存在 → 404 + ``detail.code == "ipo_not_found"``

B. HK seed (no DB):
   1. seed 命中 → 返回 IPODetail 形, sponsors/prospectus 都是 None,
      highlights/risks 都是空 list (seed 没有这些信息)
   2. 不存在 → 404

C. 缓存:
   1. 首次 detail 打 DB → 缓存; 同 code 二次 detail 不再打 DB
   2. 不同 code → 重新打 DB

D. 路由响应 schema:
   - body 必须含: ``code`` / ``name`` / ``market`` / ``industry`` / ``sponsors`` /
     ``underwriters`` / ``prospectus_url`` / ``highlights`` / ``risks`` /
     ``financial_summary``
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
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.db.base import get_engine
from app.db.base import get_session_factory as _get_factory_lru
from app.db.models import IPO
from app.main import create_app
from app.schemas.ipo import IPOItem
from app.services import ipo_ingest_service, ipo_service

# ─── 共享 fixture ─────────────────────────────────────────


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
    """无 DB client (HK seed / 路由 404 用)."""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ─── DB-backed (BE-S2-000 起 HK 也走这条) ─────────────────────────────────


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
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _seed_a(
    code: str,
    *,
    name: str = "测试股份",
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
        listing_date=listing_date or date(2025, 1, 1),
        pe_ratio=Decimal("23.45"),
        status=status,  # type: ignore[arg-type]
        data_source="test",
        updated_at=datetime.now(UTC).replace(tzinfo=None),
    )


# ─── A. A 股详情 happy ───


@pytest.mark.db
async def test_detail_a_full_merge_from_orm_and_extra(
    db_app_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """灌一行 + 手动写 sponsors/prospectus_url/extra → 详情字段全部对齐."""
    code = "600001.SH"
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(
            session, [_seed_a(code, name="贵州茅台-测试")]
        )
        await session.commit()

        # 模拟运营/RAG 写入: sponsors / prospectus_url / extra.highlights/risks/financial_summary
        await session.execute(
            update(IPO)
            .where(IPO.code == code)
            .values(
                sponsors=["中金公司", "中信证券"],
                underwriters=["中金公司", "中信证券", "国泰君安"],
                prospectus_url="https://example.com/600001.pdf",
                extra={
                    "highlights": ["白酒龙头", "毛利率 90%+", "现金流强劲"],
                    "risks": ["渠道压货", "高端消费疲软"],
                    "financial_summary": {
                        "revenue_2024": 1500.0,
                        "net_profit_2024": 750.0,
                        "gross_margin": 0.91,
                    },
                    # 这条额外的 key 不应漏给客户端
                    "internal_debug_field": "should-not-leak",
                },
            )
        )
        await session.commit()

    r = await db_app_client.get(f"/api/v1/ipos/{code}")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["code"] == code
    assert body["name"] == "贵州茅台-测试"
    assert body["sponsors"] == ["中金公司", "中信证券"]
    assert body["underwriters"] == ["中金公司", "中信证券", "国泰君安"]
    assert body["prospectus_url"] == "https://example.com/600001.pdf"
    assert body["highlights"] == ["白酒龙头", "毛利率 90%+", "现金流强劲"]
    assert body["risks"] == ["渠道压货", "高端消费疲软"]
    assert body["financial_summary"] == {
        "revenue_2024": 1500.0,
        "net_profit_2024": 750.0,
        "gross_margin": 0.91,
    }
    # ipos.extra 中其它 key 不应漏给客户端
    assert "internal_debug_field" not in body
    assert "extra" not in body


@pytest.mark.db
async def test_detail_a_extra_missing_uses_safe_defaults(
    db_app_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """ingest 写进来的 row, ``extra`` 只含 ingest 自己塞的 ``one_lot_winning_rate`` 等;
    没有 highlights/risks/financial_summary 字段 → 详情返回空 list / None."""
    code = "600002.SH"
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(session, [_seed_a(code, name="平安银行")])
        await session.commit()

    r = await db_app_client.get(f"/api/v1/ipos/{code}")
    assert r.status_code == 200
    body = r.json()
    assert body["highlights"] == []
    assert body["risks"] == []
    assert body["financial_summary"] is None
    assert body["sponsors"] is None
    assert body["underwriters"] is None
    assert body["prospectus_url"] is None


@pytest.mark.db
async def test_detail_a_corrupt_extra_does_not_500(
    db_app_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``extra.highlights`` 类型不对 (str 而非 list) 时, 详情应优雅降级到 [], 不 500."""
    code = "600003.SH"
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(session, [_seed_a(code, name="X")])
        await session.commit()
        await session.execute(
            update(IPO)
            .where(IPO.code == code)
            .values(extra={"highlights": "should-be-a-list", "risks": None})
        )
        await session.commit()

    r = await db_app_client.get(f"/api/v1/ipos/{code}")
    assert r.status_code == 200
    body = r.json()
    assert body["highlights"] == []
    assert body["risks"] == []


@pytest.mark.db
async def test_detail_a_not_found_returns_404(
    db_app_client: httpx.AsyncClient,
) -> None:
    r = await db_app_client.get("/api/v1/ipos/000999.SZ")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "ipo_not_found"


# ─── C. 缓存 ───


@pytest.mark.db
async def test_detail_second_call_hits_cache(
    db_app_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code1 = "600101.SH"
    code2 = "600102.SH"
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(
            session,
            [_seed_a(code1, name="A"), _seed_a(code2, name="B")],
        )
        await session.commit()

    counter = {"db_calls": 0}
    orig = ipo_service._orm_to_detail  # type: ignore[attr-defined]

    def counting(row):
        counter["db_calls"] += 1
        return orig(row)

    monkeypatch.setattr(ipo_service, "_orm_to_detail", counting)

    r1 = await db_app_client.get(f"/api/v1/ipos/{code1}")
    assert r1.status_code == 200
    assert counter["db_calls"] == 1

    # 同 code: 缓存命中
    r1b = await db_app_client.get(f"/api/v1/ipos/{code1}")
    assert r1b.status_code == 200
    assert counter["db_calls"] == 1, "同 code 第二次必须命中详情缓存, 不再 ORM hydrate"
    assert r1.json() == r1b.json()

    # 不同 code: 重新打 DB
    r2 = await db_app_client.get(f"/api/v1/ipos/{code2}")
    assert r2.status_code == 200
    assert counter["db_calls"] == 2


# ─── B. HK cold-start fallback / DB 命中 (BE-S2-000) ───


@pytest.mark.db
async def test_detail_hk_db_empty_falls_back_to_cold_start_seed(
    db_app_client: httpx.AsyncClient,
) -> None:
    """DB 空 + HK code 命中 cold-start seed → 200 + IPODetail 形."""
    r = await db_app_client.get("/api/v1/ipos/02015.HK")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "02015.HK"
    assert body["name"] == "理想汽车-W"
    assert body["market"] == "HK"
    for k in (
        "prospectus_url",
        "sponsors",
        "underwriters",
        "highlights",
        "risks",
        "financial_summary",
    ):
        assert k in body, f"IPODetail 必须暴露 {k} 字段, 实际 keys={list(body)}"
    assert body["highlights"] == []
    assert body["risks"] == []
    assert body["financial_summary"] is None


@pytest.mark.db
async def test_detail_hk_not_found_returns_404(
    db_app_client: httpx.AsyncClient,
) -> None:
    r = await db_app_client.get("/api/v1/ipos/99999.HK")
    assert r.status_code == 404
    body = r.json()
    assert body["detail"]["code"] == "ipo_not_found"


@pytest.mark.db
async def test_detail_404_is_not_cached(
    db_app_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``@cached(skip_if_none=True)`` 默认开, 不存在时不缓存; 否则后来 upsert 进去
    了用户还是看不见."""
    code = "600999.SH"

    # 第一次: 不存在 → 404
    r1 = await db_app_client.get(f"/api/v1/ipos/{code}")
    assert r1.status_code == 404

    # ingest 把它入库
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(
            session, [_seed_a(code, name="新上市")]
        )
        await session.commit()

    # 再请求: 必须能拿到, 而不是被前一次的 None 缓存住
    r2 = await db_app_client.get(f"/api/v1/ipos/{code}")
    assert r2.status_code == 200, r2.text
    assert r2.json()["name"] == "新上市"
