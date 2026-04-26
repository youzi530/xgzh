"""BE-007: IPO 入库 + APScheduler 调度 测试.

覆盖:

A. ``upsert_ipos`` (DB 集成, ``@pytest.mark.db``):
   1. 第一次跑: 全部 INSERT, ``inserted == len(items)``, ``created_at`` 落地
   2. 第二次跑同样的 (code, market) 但 issue_price / status 改了:
      - 行数没变 (count(*) 不增长), 验证唯一约束生效 → 走 UPDATE 分支
      - issue_price / status 被覆盖
      - ``updated_at`` > 第一次的 ``updated_at``
      - ``created_at`` 没动
   3. NULL 保护: 第二次抓的某字段 (industry / pe_ratio) 为 None 时, 旧值不被擦
   4. 空列表早 return, 不打 DB

B. ``run_ingest_a_job`` (DB 集成, ``@pytest.mark.db``):
   1. monkey-patch ``akshare_client.fetch_a_ipos`` 返回固定 fixture, 跑一次
      → DB 出现对应行
   2. fetch 抛异常 → 不冒泡, ``stats["errors"] == 1``
   3. fetch 返回空 → ``stats["received"] == 0``, 不报错

C. scheduler register (无 DB, 纯 unit):
   1. ``register_jobs`` 注册 2 个 job: ``ipo_ingest_a_initial`` + ``ipo_ingest_a_cron``
   2. ``ipo_ingest_initial_delay_seconds=0`` 时只剩 cron 一个

ENV 要求: ``XGZH_TEST_DATABASE_URL`` (A/B), 否则 A/B 自动 skip; C 总是跑.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters import akshare_client
from app.core.config import Settings
from app.db.base import get_engine
from app.db.base import get_session_factory as _get_factory_lru
from app.db.models import IPO
from app.scheduler import register_jobs
from app.schemas.ipo import IPOItem
from app.services import ipo_ingest_service


# ───────────────────────── DB fixtures (与 test_invite 同源) ─────────────────────────


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
    """让 ``run_ingest_a_job`` 用测试库的 factory, 而不是 lru_cache 里的真库 factory."""
    _get_factory_lru.cache_clear()
    get_engine.cache_clear()

    import app.db as db_pkg
    import app.services.ipo_ingest_service as ingest_mod

    orig_pkg = db_pkg.get_session_factory
    orig_mod = ingest_mod.get_session_factory
    db_pkg.get_session_factory = lambda: session_factory  # type: ignore[assignment]
    ingest_mod.get_session_factory = lambda: session_factory  # type: ignore[assignment]
    try:
        yield
    finally:
        db_pkg.get_session_factory = orig_pkg
        ingest_mod.get_session_factory = orig_mod
        _get_factory_lru.cache_clear()
        get_engine.cache_clear()


# ───────────────────────── helpers ─────────────────────────


def _item(
    code: str,
    *,
    name: str = "测试股份",
    market: str = "A",
    industry: str | None = "信息技术",
    issue_price: str | None = "10.00",
    listing_date: date | None = None,
    pe_ratio: str | None = "23.45",
    status: str = "listed",
) -> IPOItem:
    return IPOItem(
        code=code,
        name=name,
        market=market,  # type: ignore[arg-type]
        industry=industry,
        issue_price=Decimal(issue_price) if issue_price else None,
        issue_currency="CNY" if market == "A" else "HKD",
        listing_date=listing_date or date(2024, 6, 1),
        pe_ratio=Decimal(pe_ratio) if pe_ratio else None,
        status=status,  # type: ignore[arg-type]
        data_source="test",
        updated_at=datetime.now(UTC).replace(tzinfo=None),
    )


# =====================================================================
# A. upsert_ipos
# =====================================================================


@pytest.mark.db
async def test_upsert_empty_list_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipos: None,  # noqa: ARG001
) -> None:
    async with session_factory() as session:
        stats = await ipo_ingest_service.upsert_ipos(session, [])
        await session.commit()
    assert stats == {"received": 0, "inserted": 0, "updated": 0, "skipped": 0}


@pytest.mark.db
async def test_upsert_first_pass_all_inserted(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipos: None,  # noqa: ARG001
) -> None:
    items = [
        _item("600519.SH", name="贵州茅台", issue_price="100.00"),
        _item("000001.SZ", name="平安银行", issue_price="20.50"),
    ]
    async with session_factory() as session:
        stats = await ipo_ingest_service.upsert_ipos(session, items)
        await session.commit()

        rows = (await session.execute(select(IPO))).scalars().all()

    assert stats["received"] == 2
    assert stats["inserted"] == 2
    assert stats["updated"] == 0
    assert {r.code for r in rows} == {"600519.SH", "000001.SZ"}
    for r in rows:
        assert r.created_at is not None
        assert r.updated_at is not None


@pytest.mark.db
async def test_upsert_second_pass_updates_in_place(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipos: None,  # noqa: ARG001
) -> None:
    code = "600519.SH"
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(
            session, [_item(code, name="贵州茅台", issue_price="100.00", status="upcoming")]
        )
        await session.commit()
        first = (
            await session.execute(select(IPO).where(IPO.code == code))
        ).scalar_one()
        first_created = first.created_at
        first_updated = first.updated_at
        first_id = first.ipo_id

    # 同一 (code, market) 第二次, 改了 issue_price + status
    await asyncio.sleep(0.01)
    async with session_factory() as session:
        stats = await ipo_ingest_service.upsert_ipos(
            session,
            [_item(code, name="贵州茅台-新", issue_price="1888.00", status="listed")],
        )
        await session.commit()
        rows = (await session.execute(select(IPO))).scalars().all()
        second = (
            await session.execute(select(IPO).where(IPO.code == code))
        ).scalar_one()

    assert len(rows) == 1, "唯一约束 (code, market) 没生效, 第二次插了重复"
    assert stats["inserted"] == 0
    assert stats["updated"] == 1
    assert second.ipo_id == first_id, "PK 不能换"
    assert second.created_at == first_created, "created_at 不能被刷掉"
    assert second.updated_at >= first_updated, "updated_at 必须前移"
    assert second.issue_price == Decimal("1888.00")
    assert second.status == "listed"
    assert second.name == "贵州茅台-新"


@pytest.mark.db
async def test_upsert_null_in_new_does_not_overwrite_existing(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipos: None,  # noqa: ARG001
) -> None:
    """COALESCE 兜底: 第二次抓的 industry / pe_ratio = None 时, 不能擦掉旧值."""
    code = "600519.SH"
    async with session_factory() as session:
        await ipo_ingest_service.upsert_ipos(
            session,
            [_item(code, industry="食品饮料", pe_ratio="35.5")],
        )
        await session.commit()

    async with session_factory() as session:
        # industry / pe_ratio 都是 None
        await ipo_ingest_service.upsert_ipos(
            session,
            [_item(code, industry=None, pe_ratio=None)],
        )
        await session.commit()

        row = (
            await session.execute(select(IPO).where(IPO.code == code))
        ).scalar_one()

    assert row.industry_l1 == "食品饮料", "新值是 None 不应该覆盖旧 industry"
    assert row.pe_ratio == Decimal("35.5"), "新值是 None 不应该覆盖旧 pe_ratio"


# =====================================================================
# B. run_ingest_a_job (高层入口, mock akshare)
# =====================================================================


@pytest.mark.db
async def test_run_ingest_a_job_happy_path(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipos: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_items = [
        _item("600519.SH", name="贵州茅台"),
        _item("000001.SZ", name="平安银行"),
    ]

    async def fake_fetch(limit: int = 200) -> list[IPOItem]:
        assert limit == 200
        return fake_items

    monkeypatch.setattr(akshare_client, "fetch_a_ipos", fake_fetch)

    stats = await ipo_ingest_service.run_ingest_a_job()

    assert stats["received"] == 2
    assert stats["inserted"] == 2
    assert stats["errors"] == 0

    async with session_factory() as session:
        rows = (await session.execute(select(IPO))).scalars().all()
    assert {r.code for r in rows} == {"600519.SH", "000001.SZ"}


@pytest.mark.db
async def test_run_ingest_a_job_swallows_fetch_error(
    truncate_ipos: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(limit: int = 200) -> list[IPOItem]:
        raise RuntimeError("network reset")

    monkeypatch.setattr(akshare_client, "fetch_a_ipos", boom)

    # 不应抛
    stats = await ipo_ingest_service.run_ingest_a_job()

    assert stats["errors"] == 1
    assert stats["received"] == 0
    assert stats["inserted"] == 0


@pytest.mark.db
async def test_run_ingest_a_job_empty_result_is_safe(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipos: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def empty(limit: int = 200) -> list[IPOItem]:
        return []

    monkeypatch.setattr(akshare_client, "fetch_a_ipos", empty)

    stats = await ipo_ingest_service.run_ingest_a_job()
    assert stats["received"] == 0
    assert stats["errors"] == 0

    async with session_factory() as session:
        cnt = (await session.execute(text("SELECT count(*) FROM ipos"))).scalar_one()
    assert cnt == 0


# =====================================================================
# C. scheduler register (纯单元, 不需要 DB / Redis)
# =====================================================================


def _make_settings(
    *,
    initial_delay: int = 5,
    cron_hours: str = "8,20",
    tz: str = "Asia/Shanghai",
) -> Settings:
    """造一个内存 Settings, 不读 .env."""
    return Settings(
        scheduler_enabled=True,
        ipo_ingest_initial_delay_seconds=initial_delay,
        ipo_ingest_cron_hours=cron_hours,
        ipo_ingest_timezone=tz,
        ipo_ingest_a_limit=200,
    )


def test_register_jobs_with_initial_delay_registers_two() -> None:
    from app.scheduler import _build_scheduler  # type: ignore[attr-defined]

    settings = _make_settings(initial_delay=5)
    scheduler = _build_scheduler(settings)
    register_jobs(scheduler, settings)

    ids = {j.id for j in scheduler.get_jobs()}
    assert ids == {"ipo_ingest_a_initial", "ipo_ingest_a_cron"}


def test_register_jobs_zero_delay_only_cron() -> None:
    from app.scheduler import _build_scheduler  # type: ignore[attr-defined]

    settings = _make_settings(initial_delay=0)
    scheduler = _build_scheduler(settings)
    register_jobs(scheduler, settings)

    ids = {j.id for j in scheduler.get_jobs()}
    assert ids == {"ipo_ingest_a_cron"}


def test_register_jobs_is_reentrant() -> None:
    from app.scheduler import _build_scheduler  # type: ignore[attr-defined]

    settings = _make_settings(initial_delay=5)
    scheduler = _build_scheduler(settings)
    register_jobs(scheduler, settings)
    register_jobs(scheduler, settings)

    ids = [j.id for j in scheduler.get_jobs()]
    assert sorted(ids) == ["ipo_ingest_a_cron", "ipo_ingest_a_initial"], ids
