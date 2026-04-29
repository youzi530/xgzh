"""extend ipos with price_min / price_max for HK IPO price range (BUG-S6.8-004).

Revision ID: 0015_ipos_price_range
Revises: 0014_community
Create Date: 2026-04-29

背景
====
Sprint 6.8 用户上报 bug ④: 申购中港股发行价应展示**区间** (例 ``166.60-183.20``).
spike 显示东财 ``ipolist.html`` 招股价列**本身就有区间字符串** (50/50 行都是
``"x-y"`` 格式, 即使最终单值也写成 ``"24.86-24.86"``), 但现 adapter
``_parse_issue_price`` 实现取上限丢了下限:

::

    parts = s.split("-")
    candidate = parts[-1].strip()  # "166.60-183.20" → 183.20, 丢了 166.60

修复方向 (用户决策 ``min_max_keep_legacy``):

- DB 加 ``price_min`` / ``price_max`` 双列; 老 ``issue_price`` 保留 = ``price_max``
  让老 client 不破 (升限价对齐 ``raised_amount`` 算口径)
- adapter 拆双值; AAStocks 单值场景写 ``min == max``; 真区间则两列分别写
- FE 检测 ``price_min != price_max`` 显示区间字符串 ``"166.60 - 183.20 港元"``,
  否则单值 ``"183.20 港元"``

新加列
======
- ``price_min NUMERIC(12,4)`` — 招股价下限 (与 issue_price 同精度)
- ``price_max NUMERIC(12,4)`` — 招股价上限

回填策略 (本迁移内执行)
=======================
``UPDATE ipos SET price_min = issue_price, price_max = issue_price
 WHERE issue_price IS NOT NULL`` — 历史数据没区间信息, 单值灌进 min/max. 真有区间
的老数据要等下一轮 ingest 把 ``price_min`` 重新刷成下限 (历史数据被东财 ipolist
覆盖一次后 min/max 就齐了).

不加索引
=========
价格区间 query 极少作为筛选 (``GET /ipos`` 主要按 status / industry / market 筛),
临时按价格筛走 issue_price 列已足够. 索引留 Sprint 7 加。

回滚
=====
``downgrade()`` DROP COLUMN; 老数据零损 (``issue_price`` 仍在).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_ipos_price_range"
down_revision: str | None = "0014_community"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ipos",
        sa.Column(
            "price_min",
            sa.Numeric(12, 4),
            nullable=True,
            comment="招股价下限 (港股区间下端; 单值 IPO 则 == price_max)",
        ),
    )
    op.add_column(
        "ipos",
        sa.Column(
            "price_max",
            sa.Numeric(12, 4),
            nullable=True,
            comment="招股价上限 (== legacy issue_price; 升限价对齐 raised_amount)",
        ),
    )

    # 回填: 用 issue_price 灌进 min/max (历史数据没区间, 假设单值)
    op.execute(
        """
        UPDATE ipos
        SET price_min = issue_price,
            price_max = issue_price
        WHERE issue_price IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.drop_column("ipos", "price_max")
    op.drop_column("ipos", "price_min")
