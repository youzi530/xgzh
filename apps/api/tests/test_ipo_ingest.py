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
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
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
    """A 股: 启动延迟 > 0 时, ``initial`` + ``cron`` 都该挂上.

    BE-S2-000 起 register_jobs 同时注册 HK jobs, 这里只验"A 系列存在", 不全等.
    HK 系列由 ``test_register_jobs_includes_hk`` 单独覆盖.
    """
    from app.scheduler import _build_scheduler  # type: ignore[attr-defined]

    settings = _make_settings(initial_delay=5)
    scheduler = _build_scheduler(settings)
    register_jobs(scheduler, settings)

    ids = {j.id for j in scheduler.get_jobs()}
    assert {"ipo_ingest_a_initial", "ipo_ingest_a_cron"}.issubset(ids), ids


def test_register_jobs_zero_delay_only_cron() -> None:
    """A 股: 启动延迟 = 0 时, 仅 ``cron`` 挂上, ``initial`` 不挂."""
    from app.scheduler import _build_scheduler  # type: ignore[attr-defined]

    settings = _make_settings(initial_delay=0)
    scheduler = _build_scheduler(settings)
    register_jobs(scheduler, settings)

    ids = {j.id for j in scheduler.get_jobs()}
    assert "ipo_ingest_a_cron" in ids
    assert "ipo_ingest_a_initial" not in ids


def test_register_jobs_is_reentrant() -> None:
    """重复 ``register_jobs`` 不该让任何 job 重复出现."""
    from app.scheduler import _build_scheduler  # type: ignore[attr-defined]

    settings = _make_settings(initial_delay=5)
    scheduler = _build_scheduler(settings)
    register_jobs(scheduler, settings)
    register_jobs(scheduler, settings)

    ids = [j.id for j in scheduler.get_jobs()]
    # 用 set: 重入后 ids 应等于 set(ids); 不允许 duplicate
    assert len(ids) == len(set(ids)), f"duplicates after re-register: {ids}"
    assert {"ipo_ingest_a_initial", "ipo_ingest_a_cron"}.issubset(set(ids))


# =====================================================================
# D. BE-S2-000: HK ingest (run_ingest_hk_job + scheduler hk jobs)
# =====================================================================


def _hk_item(
    code: str,
    *,
    name: str = "AP-公司",
    industry: str | None = None,
    listing_date: date | None = None,
    status: str = "upcoming",
) -> IPOItem:
    """构造一个 HK 申请人风格 IPOItem (code 用 AP- 占位)."""
    return _item(
        code,
        name=name,
        market="HK",
        industry=industry,
        issue_price=None,  # 申请阶段无发行价
        listing_date=listing_date,
        pe_ratio=None,
        status=status,
    )


@pytest.mark.db
async def test_run_ingest_hk_job_happy_with_prospectus_url(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipos: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BE-S2-000: HK ingest 写库后, ``ipos.extra.prospectus_url`` 也得落上."""
    from app.adapters import hkex_client
    from app.adapters.hkex_client import HKApplicantFetchResult

    fake_items = [
        _hk_item("AP260301LIBAN.HK", name="利邦控股有限公司"),
        _hk_item("AP260215MUDAS.HK", name="某大科技股份有限公司"),
    ]
    fake_pdf_map = {
        "AP260301LIBAN.HK": "https://www1.hkexnews.hk/path/libang.pdf",
        "AP260215MUDAS.HK": "https://www1.hkexnews.hk/path/mouda.pdf",
    }

    async def fake_fetch(*, settings=None, limit=None) -> HKApplicantFetchResult:
        return HKApplicantFetchResult(items=fake_items, prospectus_urls=fake_pdf_map)

    monkeypatch.setattr(hkex_client, "fetch_hk_applicants", fake_fetch)

    stats = await ipo_ingest_service.run_ingest_hk_job()

    assert stats["received"] == 2
    assert stats["inserted"] == 2
    assert stats["errors"] == 0
    assert stats["with_pdf"] == 2

    async with session_factory() as session:
        rows = (await session.execute(select(IPO).order_by(IPO.code))).scalars().all()
    assert {r.code for r in rows} == set(fake_pdf_map)
    for r in rows:
        # extra.prospectus_url 通过侧通道写进来了
        assert isinstance(r.extra, dict)
        assert r.extra["prospectus_url"] == fake_pdf_map[r.code]
        # 同时 ingest 自己塞的 one_lot_winning_rate 也在 (jsonb merge 没擦掉)
        assert "one_lot_winning_rate" in r.extra


@pytest.mark.db
async def test_run_ingest_hk_job_swallows_fetch_error(
    truncate_ipos: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch 抛 → 不冒泡, ``stats["errors"] == 1`` (与 A 股 run_ingest_a_job 行为一致)."""
    from app.adapters import hkex_client

    async def boom(*, settings=None, limit=None):
        raise RuntimeError("hkexnews IP banned")

    monkeypatch.setattr(hkex_client, "fetch_hk_applicants", boom)

    stats = await ipo_ingest_service.run_ingest_hk_job()
    assert stats["errors"] == 1
    assert stats["received"] == 0
    assert stats["inserted"] == 0


@pytest.mark.db
async def test_run_ingest_hk_job_empty_result_is_safe(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipos: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch 返回空 → ``stats["received"] == 0``, DB 一行没多."""
    from app.adapters import hkex_client
    from app.adapters.hkex_client import HKApplicantFetchResult

    async def empty(*, settings=None, limit=None) -> HKApplicantFetchResult:
        return HKApplicantFetchResult.empty()

    monkeypatch.setattr(hkex_client, "fetch_hk_applicants", empty)

    stats = await ipo_ingest_service.run_ingest_hk_job()
    assert stats["received"] == 0
    assert stats["errors"] == 0

    async with session_factory() as session:
        cnt = (
            await session.execute(text("SELECT count(*) FROM ipos WHERE market='HK'"))
        ).scalar_one()
    assert cnt == 0


@pytest.mark.db
async def test_run_ingest_hk_job_extra_merge_preserves_be_s2_004_fields(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipos: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BE-S2-000 关键不变量: HK ingest 第二次跑时, BE-S2-004 RAG 写入 ``extra.highlights``
    等字段不能被覆盖 (用 PG ``jsonb || jsonb`` merge 操作符).

    场景:
    1. 第一次 ingest: extra = {one_lot_winning_rate, schema_updated_at, prospectus_url}
    2. 模拟 BE-S2-004 后续写: extra.highlights / extra.risks
    3. 第二次 ingest 同 code: extra 应保留 highlights / risks, 同时
       prospectus_url 被新值覆盖
    """
    from app.adapters import hkex_client
    from app.adapters.hkex_client import HKApplicantFetchResult

    code = "AP260301LIBAN.HK"

    async def fetch_v1(*, settings=None, limit=None) -> HKApplicantFetchResult:
        return HKApplicantFetchResult(
            items=[_hk_item(code, name="利邦控股有限公司")],
            prospectus_urls={code: "https://example.com/v1.pdf"},
        )

    monkeypatch.setattr(hkex_client, "fetch_hk_applicants", fetch_v1)
    await ipo_ingest_service.run_ingest_hk_job()

    # BE-S2-004 后续写 highlights / risks (模拟)
    from sqlalchemy import update as sql_update

    async with session_factory() as session:
        await session.execute(
            sql_update(IPO)
            .where(IPO.code == code)
            .values(
                extra={
                    "highlights": ["招股书亮点1", "亮点2"],
                    "risks": ["风险1"],
                    "prospectus_url": "https://example.com/v1.pdf",
                }
            )
        )
        await session.commit()

    # 第二次 ingest, prospectus_url 改了
    async def fetch_v2(*, settings=None, limit=None) -> HKApplicantFetchResult:
        return HKApplicantFetchResult(
            items=[_hk_item(code, name="利邦控股有限公司")],
            prospectus_urls={code: "https://example.com/v2.pdf"},
        )

    monkeypatch.setattr(hkex_client, "fetch_hk_applicants", fetch_v2)
    await ipo_ingest_service.run_ingest_hk_job()

    async with session_factory() as session:
        row = (
            await session.execute(select(IPO).where(IPO.code == code))
        ).scalar_one()

    extra = row.extra
    assert isinstance(extra, dict)
    # 老 RAG 字段保留 (jsonb merge)
    assert extra["highlights"] == ["招股书亮点1", "亮点2"]
    assert extra["risks"] == ["风险1"]
    # 新 ingest 字段覆盖
    assert extra["prospectus_url"] == "https://example.com/v2.pdf"
    # ingest 自己塞的字段也在
    assert "one_lot_winning_rate" in extra


# ─── scheduler register: hk jobs ───────────────────────────────────────


def test_register_jobs_includes_hk_initial_and_cron() -> None:
    """BE-S2-000: ``register_jobs`` 必须同时挂上 hk_ingest_initial + hk_ingest_cron."""
    from app.scheduler import _build_scheduler  # type: ignore[attr-defined]

    settings = Settings(
        scheduler_enabled=True,
        ipo_ingest_initial_delay_seconds=5,
        ipo_ingest_cron_hours="8,20",
        ipo_ingest_timezone="Asia/Shanghai",
        ipo_ingest_a_limit=200,
        ipo_ingest_hk_initial_delay_seconds=10,
        ipo_ingest_hk_cron_hours="9,17",
        ipo_ingest_hk_timezone="Asia/Hong_Kong",
        ipo_ingest_hk_limit=100,
    )
    scheduler = _build_scheduler(settings)
    register_jobs(scheduler, settings)

    ids = {j.id for j in scheduler.get_jobs()}
    assert "ipo_ingest_hk_initial" in ids
    assert "ipo_ingest_hk_cron" in ids


def test_register_jobs_hk_zero_delay_only_cron() -> None:
    """``ipo_ingest_hk_initial_delay_seconds=0`` 时仅挂 cron, 不挂 initial."""
    from app.scheduler import _build_scheduler  # type: ignore[attr-defined]

    settings = Settings(
        scheduler_enabled=True,
        ipo_ingest_initial_delay_seconds=5,
        ipo_ingest_cron_hours="8,20",
        ipo_ingest_timezone="Asia/Shanghai",
        ipo_ingest_a_limit=200,
        ipo_ingest_hk_initial_delay_seconds=0,
        ipo_ingest_hk_cron_hours="9,17",
        ipo_ingest_hk_timezone="Asia/Hong_Kong",
        ipo_ingest_hk_limit=100,
    )
    scheduler = _build_scheduler(settings)
    register_jobs(scheduler, settings)

    ids = {j.id for j in scheduler.get_jobs()}
    assert "ipo_ingest_hk_cron" in ids
    assert "ipo_ingest_hk_initial" not in ids


# =====================================================================
# E. BE-S3-002: 文章 ingest scheduler 注册
# =====================================================================


def test_register_jobs_includes_article_ingest_initial_and_cron() -> None:
    """BE-S3-002: register_jobs 必须挂 article_ingest_initial + article_ingest_cron."""
    from app.scheduler import _build_scheduler  # type: ignore[attr-defined]

    settings = Settings(
        scheduler_enabled=True,
        ipo_ingest_initial_delay_seconds=5,
        ipo_ingest_cron_hours="8,20",
        ipo_ingest_timezone="Asia/Shanghai",
        ipo_ingest_a_limit=200,
        ipo_ingest_hk_initial_delay_seconds=10,
        ipo_ingest_hk_cron_hours="9,17",
        ipo_ingest_hk_timezone="Asia/Hong_Kong",
        ipo_ingest_hk_limit=100,
        article_ingest_initial_delay_seconds=15,
        article_ingest_cron_expr="0",
    )
    scheduler = _build_scheduler(settings)
    register_jobs(scheduler, settings)

    ids = {j.id for j in scheduler.get_jobs()}
    assert "article_ingest_initial" in ids
    assert "article_ingest_cron" in ids


def test_register_jobs_article_zero_delay_only_cron() -> None:
    """``article_ingest_initial_delay_seconds=0`` 时仅挂 cron, 不挂 initial."""
    from app.scheduler import _build_scheduler  # type: ignore[attr-defined]

    settings = Settings(
        scheduler_enabled=True,
        ipo_ingest_initial_delay_seconds=5,
        ipo_ingest_cron_hours="8,20",
        ipo_ingest_timezone="Asia/Shanghai",
        ipo_ingest_a_limit=200,
        ipo_ingest_hk_initial_delay_seconds=10,
        ipo_ingest_hk_cron_hours="9,17",
        ipo_ingest_hk_timezone="Asia/Hong_Kong",
        ipo_ingest_hk_limit=100,
        article_ingest_initial_delay_seconds=0,
        article_ingest_cron_expr="0",
    )
    scheduler = _build_scheduler(settings)
    register_jobs(scheduler, settings)

    ids = {j.id for j in scheduler.get_jobs()}
    assert "article_ingest_cron" in ids
    assert "article_ingest_initial" not in ids
