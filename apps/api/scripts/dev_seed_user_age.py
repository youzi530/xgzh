"""一次性把现有 ``users.created_at`` 回退 30 天 (BUG-S6.6-002c).

背景
====
Sprint 6 BE-S6-009 引入 "新用户 7d 只读"; 但 dev 环境的测试账号都是当天注册,
导致 Sprint 6.5 用户验收时 "发布帖子" 全部 403.

Sprint 6.6 已经把 ``COMMUNITY_NEW_USER_READONLY_DAYS`` 配置化了 (dev .env=0),
但**老的 .env 不更新**也能直接修复 → 跑这个脚本一次, 把 created_at 回退 30 天,
保护期就算开启也已经过. 双保险.

用法
====
.. code-block:: bash

    cd apps/api
    uv run python -m scripts.dev_seed_user_age

防误跑
======
- 只能在 ``APP_ENV != prod`` 下跑; 生产环境直接 sys.exit(1).
- 默认 dry-run, 必须加 ``--apply`` 才真改.

可重入
======
重复跑会**累积**回退(每次再 -30 天). 只在你明确想"再退一次"时跑.
正常情况 dev 环境跑一次就够.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import User


async def main() -> int:
    parser = argparse.ArgumentParser(description="dev: nudge users.created_at backwards by N days")
    parser.add_argument("--days", type=int, default=30, help="回退天数 (默认 30)")
    parser.add_argument("--apply", action="store_true", help="真改 (默认 dry-run)")
    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env == "prod":
        logger.error("dev_seed_user_age refuses to run in app_env=prod")
        return 1

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(select(User.user_id, User.created_at, User.nickname))).all()
        if not rows:
            print("no users found, nothing to do")
            return 0

        print(f"app_env={settings.app_env} found {len(rows)} users:")
        for r in rows:
            new_ts = r.created_at - timedelta(days=args.days)
            print(f"  {r.user_id}  {r.nickname or '(unnamed)':<20s}  {r.created_at} → {new_ts}")

        if not args.apply:
            print(f"\n(dry-run) re-run with --apply to actually nudge {args.days} days")
            return 0

        await session.execute(
            update(User).values(created_at=User.created_at - timedelta(days=args.days))
        )
        await session.commit()
        print(f"\n✅ nudged {len(rows)} users back by {args.days} days")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
