"""``scripts/seed_brokers.py`` 端到端集成测.

覆盖:
1. seeds/brokers.json 真实文件 → 校验通过 (端到端校验种子文件)
2. dry-run 不写库
3. happy path: insert 全部
4. 二次运行 (无变化) → 全 update, 计数仍 7+
5. seeds 改了某 slug 的 fee → 二次运行 update 该字段
6. seeds 不再含某 slug → 不会被删 (脚本只新增/更新, 不删除)
7. 校验失败: partnership_type=NONE 但有 cpa → 不写任何一行
8. 校验失败: slug 重复 → raise
9. ``invalidate_namespace`` 在 upsert 后被调 (验证 cache 钩子)

注: 7-8 走单元测 (``load_seed`` 直接拿)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache import set_redis_client
from app.cache.redis_client import InMemoryRedisClient
from app.db.models import Broker
from scripts.seed_brokers import load_seed, run, upsert_brokers

pytestmark = pytest.mark.db


REPO_SEED = (
    Path(__file__).resolve().parents[2] / "seeds" / "brokers.json"
)


def _minimal_row(slug: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "slug": slug,
        "name_zh": slug.upper(),
        "name_en": None,
        "logo_url": None,
        "market_support": ["HK"],
        "licenses": ["SFC-1"],
        "fees": {"hk_commission_rate": 0.0003},
        "features": {"ipo_subscription": True},
        "promotion": {"is_active": False, "title": "", "referral_url": None},
        "partnership_type": "NONE",
        "partnership_cpa_amount": None,
        "partnership_cps_rate": None,
        "display_order": 50,
        "is_active": True,
    }
    base.update(overrides)
    return base


# ─── 1. 真实 seeds 文件校验 ───────────────────────────────────────────────


def test_repo_seed_file_validates() -> None:
    """seeds/brokers.json 必须永远通过校验 (CI 守门, 防误改).

    单测只读文件 + 校验, 不写 DB; 不依赖 db fixture.
    """
    rows = load_seed(REPO_SEED)
    assert len(rows) >= 6, "seed 至少 6 家券商 (spec 要求 6-8)"
    slugs = [r["slug"] for r in rows]
    assert len(set(slugs)) == len(slugs), f"slug 不应重复, got {slugs}"


# ─── 2. dry-run / 端到端 upsert 走真实 DB ──────────────────────────────────


async def test_run_dry_run_does_not_write_db(
    truncate_all: None,  # noqa: ARG001
    redis_client: InMemoryRedisClient,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """2. dry-run 不写库, 计数 = 0."""
    seed = tmp_path / "brokers.json"
    seed.write_text(
        json.dumps([_minimal_row("dry-run-broker")]),
        encoding="utf-8",
    )
    code = await run(seed, dry_run=True)
    assert code == 0

    async with session_factory() as s:
        rows = (await s.execute(select(Broker))).scalars().all()
        assert rows == [], "dry_run 不能写入"


async def test_upsert_inserts_then_updates_idempotent(
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """3 + 4. 首次全 insert; 二次相同输入全 update; 总数不变."""
    rows = [_minimal_row("a", display_order=10), _minimal_row("b", display_order=20)]

    inserted, updated = await upsert_brokers(rows)
    assert (inserted, updated) == (2, 0)

    inserted2, updated2 = await upsert_brokers(rows)
    assert (inserted2, updated2) == (0, 2)

    async with session_factory() as s:
        count = len((await s.execute(select(Broker))).scalars().all())
        assert count == 2


async def test_upsert_updates_changed_fields(
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """5. seeds 改了 fees → 二次运行 update 字段."""
    await upsert_brokers([_minimal_row("c", display_order=10)])
    await upsert_brokers(
        [_minimal_row("c", display_order=99, fees={"hk_commission_rate": 0.0005})]
    )

    async with session_factory() as s:
        b = (
            await s.execute(select(Broker).where(Broker.slug == "c"))
        ).scalar_one()
        assert b.display_order == 99
        assert b.fees["hk_commission_rate"] == 0.0005


async def test_upsert_does_not_delete_missing_slug(
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """6. seeds 后续不再含某 slug → 不会删除 (只新增 + 更新)."""
    await upsert_brokers([_minimal_row("d"), _minimal_row("e")])
    await upsert_brokers([_minimal_row("d")])  # e 不在新 seed 里

    async with session_factory() as s:
        slugs = [
            b.slug for b in (await s.execute(select(Broker))).scalars().all()
        ]
        assert sorted(slugs) == ["d", "e"]


# ─── 7-8. load_seed 校验异常 ──────────────────────────────────────────────


def test_load_seed_rejects_partnership_consistency(tmp_path: Path) -> None:
    """7. partnership=NONE 但有 cpa → ValueError."""
    seed = tmp_path / "bad.json"
    seed.write_text(
        json.dumps(
            [
                _minimal_row(
                    "bad", partnership_type="NONE", partnership_cpa_amount=100
                )
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cpa_amount"):
        load_seed(seed)


def test_load_seed_rejects_duplicate_slug(tmp_path: Path) -> None:
    """8. slug 重复 → ValueError."""
    seed = tmp_path / "dup.json"
    seed.write_text(
        json.dumps([_minimal_row("dup"), _minimal_row("dup", display_order=99)]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="slug 重复"):
        load_seed(seed)


def test_load_seed_rejects_invalid_market(tmp_path: Path) -> None:
    """8b. market_support 含非法值 → ValueError."""
    seed = tmp_path / "mkt.json"
    seed.write_text(
        json.dumps([_minimal_row("xx", market_support=["UK"])]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="market_support"):
        load_seed(seed)


def test_load_seed_rejects_promotion_without_https(tmp_path: Path) -> None:
    """8c. promotion.is_active=True 但 referral_url 不是 https → ValueError."""
    seed = tmp_path / "p.json"
    bad_promo = {
        "is_active": True,
        "title": "x",
        "description": "y",
        "referral_url": "http://example.com",  # http 而非 https
    }
    seed.write_text(
        json.dumps([_minimal_row("p", promotion=bad_promo)]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="https"):
        load_seed(seed)


# ─── 9. cache invalidate 钩子 ──────────────────────────────────────────────


async def test_run_invalidates_brokers_cache(
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    """9. ``run()`` 完整流程: 写库后调 invalidate_namespace 清掉 brokers:* 缓存."""
    redis = InMemoryRedisClient()
    set_redis_client(redis)
    try:
        # 模拟之前已存在的 brokers:list 缓存条目
        await redis.set("cache:brokers:list:list_brokers:fakehash", "[]")
        await redis.set("cache:brokers:detail:get_broker_detail:fakehash", "{}")

        seed = tmp_path / "brokers.json"
        seed.write_text(
            json.dumps([_minimal_row("inv-test")]), encoding="utf-8"
        )

        code = await run(seed, dry_run=False)
        assert code == 0

        # 两条 cache key 都应被清
        assert await redis.get("cache:brokers:list:list_brokers:fakehash") is None
        assert (
            await redis.get("cache:brokers:detail:get_broker_detail:fakehash") is None
        )
    finally:
        await redis.aclose()
