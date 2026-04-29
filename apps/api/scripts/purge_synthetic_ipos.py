"""一次性清掉 `data_source = 'synthetic-2026'` 的假数据 (DATA-S6.6-005).

背景
====
Sprint 1 ~ Sprint 6 历史回填脚本 ``scripts/backfill_historical_ipos.py --source synthetic``
为了让 demo / e2e 有 ≥ 600 行历史数据, 程序化合成了 280 条 ``data_source='synthetic-2026'``
的港股 IPO. Sprint 6.5 用户验收时发现首页港股新股列表全是 "AI 芯片-383" 这种合成名,
完全对不上市场实际(可孚医疗 / 商米科技-W 等).

Sprint 6.6 (spec/15) 已经把主源切到东方财富 ipolist (50 行真实新股),
现在把 synthetic 假数据全删, 让前端只看真数据.

用法
====
.. code-block:: bash

    cd apps/api

    # dry-run (默认): 只打印将删多少行, 不写库
    uv run python -m scripts.purge_synthetic_ipos

    # 真删
    uv run python -m scripts.purge_synthetic_ipos --apply

防误跑
======
- ``app_env == 'prod'`` 时必须带 ``--yes-i-am-sure-this-is-prod`` 才能真跑.
  正常情况生产应该用 backfill --source akshare 灌过真数据, 不该有 synthetic-2026 的行.
- 默认 dry-run; 必须 ``--apply`` 才真改.
- 同步级联清 ``ipo_documents`` (FK) / 缓存 invalidate.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import delete, func, select

from app.cache import invalidate_namespace
from app.core.config import get_settings
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import IPO


async def main() -> int:
    parser = argparse.ArgumentParser(description="purge synthetic-2026 IPO rows")
    parser.add_argument(
        "--data-source",
        default="synthetic-2026",
        help="要删的 data_source 标签 (默认 synthetic-2026)",
    )
    parser.add_argument("--apply", action="store_true", help="真删 (默认 dry-run)")
    parser.add_argument(
        "--yes-i-am-sure-this-is-prod",
        action="store_true",
        help="prod 环境必须显式带这个 flag",
    )
    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env == "prod" and not args.yes_i_am_sure_this_is_prod:
        logger.error(
            "purge_synthetic_ipos refuses to run in app_env=prod "
            "without --yes-i-am-sure-this-is-prod"
        )
        return 1

    factory = get_session_factory()
    async with factory() as session:
        count_stmt = select(func.count(IPO.code)).where(IPO.data_source == args.data_source)
        n = int((await session.execute(count_stmt)).scalar() or 0)
        if n == 0:
            print(f"no rows match data_source='{args.data_source}', nothing to do")
            return 0

        sample_stmt = (
            select(IPO.code, IPO.name, IPO.market, IPO.listing_date)
            .where(IPO.data_source == args.data_source)
            .limit(5)
        )
        sample = (await session.execute(sample_stmt)).all()
        print(f"app_env={settings.app_env}")
        print(f"found {n} rows with data_source='{args.data_source}', sample:")
        for row in sample:
            print(f"  {row.code:<14s} {row.market:<3s} {row.listing_date}  {row.name}")

        if not args.apply:
            print(f"\n(dry-run) re-run with --apply to delete {n} rows")
            return 0

        await session.execute(delete(IPO).where(IPO.data_source == args.data_source))
        await session.commit()

    invalidated = await invalidate_namespace("ipos:list", "ipos:detail", "ipos:historical")
    print(f"\n✅ deleted {n} rows (data_source='{args.data_source}'); cache_keys_invalidated={invalidated}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
