"""券商种子数据 upsert 脚本 (BE-S3-007).

读取 ``apps/api/seeds/brokers.json`` (6-8 家券商) → ``brokers`` 表幂等 upsert by slug.

使用方式
========
    # 默认走 settings.database_url (与 alembic 同源)
    cd apps/api && uv run python -m scripts.seed_brokers

    # 自定义种子文件
    uv run python -m scripts.seed_brokers --seed-file /path/to/brokers.json

    # 干跑 (只校验, 不写库)
    uv run python -m scripts.seed_brokers --dry-run

幂等保证
========
- ``ON CONFLICT (slug) DO UPDATE``: 每次运行都把 seeds 当真相重写所有字段;
  运营手动改过 DB 的字段也会被覆盖 (符合 seed 脚本设计预期).
- ``deleted_at = NULL`` 显式重置: 防止"曾经软删过的 slug 被重新 seed 时仍处下架"
- 缓存失效: 末尾调 ``invalidate_namespace("brokers:list" / "brokers:detail")``,
  保证 API 立即看到新数据.

校验
====
- ``slug`` 唯一性: 文件内 slug 重复直接 raise (而非 DB UNIQUE 兜底);
- ``partnership_type`` 与 ``partnership_cpa_amount/cps_rate`` 一致性 (CPA 必须
  填 cpa_amount; CPS 必须填 cps_rate; NONE 必须俩 null);
- ``market_support`` ⊆ {HK, A, US, SG};
- ``promotion.referral_url`` is_active=True 时必须为 https URL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.cache import invalidate_namespace
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import Broker

VALID_MARKETS: frozenset[str] = frozenset({"HK", "A", "US", "SG"})
VALID_PARTNERSHIP: frozenset[str] = frozenset({"CPA", "CPS", "BOTH", "NONE"})
DEFAULT_SEED_FILE = Path(__file__).resolve().parent.parent / "seeds" / "brokers.json"


def _validate_row(row: dict[str, Any]) -> None:
    """seed 文件单行业务校验; 失败直接 raise ``ValueError``."""
    slug = row.get("slug")
    if not isinstance(slug, str) or not slug:
        raise ValueError(f"missing/invalid slug: {row!r}")

    pt = row.get("partnership_type")
    if pt not in VALID_PARTNERSHIP:
        raise ValueError(f"[{slug}] partnership_type invalid: {pt!r}")

    cpa = row.get("partnership_cpa_amount")
    cps = row.get("partnership_cps_rate")

    if pt == "NONE" and (cpa is not None or cps is not None):
        raise ValueError(
            f"[{slug}] partnership_type=NONE 但 cpa_amount={cpa} cps_rate={cps}"
        )
    if pt in ("CPA", "BOTH") and cpa is None:
        raise ValueError(f"[{slug}] partnership_type={pt} 必须填 cpa_amount")
    if pt in ("CPS", "BOTH") and cps is None:
        raise ValueError(f"[{slug}] partnership_type={pt} 必须填 cps_rate")
    if cps is not None and not (0 <= float(cps) <= 1):
        raise ValueError(f"[{slug}] cps_rate={cps} 须 ∈ [0, 1]")

    markets = row.get("market_support", [])
    if not isinstance(markets, list):
        raise ValueError(f"[{slug}] market_support 必须是 list")
    invalid = set(markets) - VALID_MARKETS
    if invalid:
        raise ValueError(f"[{slug}] market_support 含非法值: {invalid}")

    promo = row.get("promotion") or {}
    if promo.get("is_active"):
        url = promo.get("referral_url") or ""
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError(
                f"[{slug}] promotion.is_active=True 时 referral_url 必须是 https"
            )


def load_seed(path: Path) -> list[dict[str, Any]]:
    """读 seed 文件 + 全量校验; 失败抛 ValueError, 不写任何一行."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} 必须是 list of brokers")

    slugs: set[str] = set()
    for row in raw:
        _validate_row(row)
        if row["slug"] in slugs:
            raise ValueError(f"slug 重复: {row['slug']}")
        slugs.add(row["slug"])

    return raw


def _to_orm_payload(row: dict[str, Any]) -> dict[str, Any]:
    """把 JSON 行翻成 ``brokers`` 表列字典 (含 ``deleted_at=None`` 显式重置)."""
    cpa = row.get("partnership_cpa_amount")
    cps = row.get("partnership_cps_rate")
    return {
        "slug": row["slug"],
        "name_zh": row["name_zh"],
        "name_en": row.get("name_en"),
        "logo_url": row.get("logo_url"),
        "market_support": row.get("market_support") or [],
        "licenses": row.get("licenses") or [],
        "fees": row.get("fees") or {},
        "features": row.get("features") or {},
        "promotion": row.get("promotion") or {},
        "partnership_type": row.get("partnership_type", "NONE"),
        "partnership_cpa_amount": Decimal(str(cpa)) if cpa is not None else None,
        "partnership_cps_rate": Decimal(str(cps)) if cps is not None else None,
        "display_order": int(row.get("display_order", 0)),
        "is_active": bool(row.get("is_active", True)),
        "deleted_at": None,
    }


async def upsert_brokers(rows: list[dict[str, Any]]) -> tuple[int, int]:
    """对每行 ``ON CONFLICT (slug) DO UPDATE`` 幂等 upsert.

    Returns:
        ``(inserted, updated)`` 计数; PG 14 ``xmax = 0`` 区分 (insert) 和 (update).
    """
    factory = get_session_factory()
    inserted = 0
    updated = 0

    async with factory() as session, session.begin():
        for payload in (_to_orm_payload(r) for r in rows):
            stmt = pg_insert(Broker.__table__).values(**payload)  # type: ignore[arg-type]
            update_payload = {
                k: stmt.excluded[k]
                for k in payload
                if k != "slug"  # PK 不重写
            }
            upsert = stmt.on_conflict_do_update(
                index_elements=["slug"], set_=update_payload
            ).returning(
                sa_text("(xmax = 0) AS inserted")
            )
            row = (await session.execute(upsert)).fetchone()
            if row is not None and bool(row[0]):
                inserted += 1
            else:
                updated += 1

    return inserted, updated


async def run(seed_file: Path, *, dry_run: bool) -> int:
    """主入口; 返进程 exit code (0 ok, 非 0 fail)."""
    if not await asyncio.to_thread(seed_file.exists):
        logger.error(f"seed_brokers.file_not_found path={seed_file}")
        return 2

    try:
        rows = await asyncio.to_thread(load_seed, seed_file)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"seed_brokers.validation_failed err={e}")
        return 3

    slugs_repr = [r["slug"] for r in rows]
    logger.info(f"seed_brokers.loaded count={len(rows)} slugs={slugs_repr}")

    if dry_run:
        logger.info(f"seed_brokers.dry_run rows={len(rows)} (no DB write)")
        return 0

    try:
        inserted, updated = await upsert_brokers(rows)
    except Exception as e:  # noqa: BLE001
        logger.error(f"seed_brokers.upsert_failed err={e}")
        return 4

    await invalidate_namespace("brokers:list")
    await invalidate_namespace("brokers:detail")

    logger.info(
        f"seed_brokers.done inserted={inserted} updated={updated} "
        f"total={inserted + updated} cache_invalidated=true"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="券商种子数据 upsert (BE-S3-007)")
    parser.add_argument(
        "--seed-file",
        type=Path,
        default=DEFAULT_SEED_FILE,
        help=f"种子 JSON 路径 (默认 {DEFAULT_SEED_FILE})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="仅校验, 不写库"
    )
    args = parser.parse_args(argv)
    return asyncio.run(run(args.seed_file, dry_run=args.dry_run))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
