"""extend ipos for historical data sink (Sprint 4 BE-S4-001):
3 new columns (first_day_change_pct / one_lot_winning_rate / oversubscribe_multiple)
+ 3 indexes for historical filtering / industry aggregation.

Revision ID: 0008_ipos_historical
Revises: 0007_vip
Create Date: 2026-04-28

背景
====
Sprint 4 §历史数据沉淀 + uCharts 散点图 + AI 规律分析报告 三条都依赖 ipos 表
"上市后回填" 字段, 0001 时只有 issue_price / pe_ratio / raised_amount 这种"上市
前已知" 字段, 上市后实际涨跌 / 中签率 / 认购倍数全靠 BE-S4-002 历史回填脚本写入.

新加列 (全部 ALTER TABLE ADD COLUMN, 全 nullable, 老行默认 NULL 不阻塞 API)
=========================================================================
- ``first_day_change_pct NUMERIC(8,4)`` — 上市首日涨跌幅 % (HK/A 通用); 范围理论
  [-100, +∞] 实务 [-30, +500], 用 (8,4) 容纳 ±9999.9999% 极端值; spec/03 §模块一
  历史 IPO 列表 / spec/04 §3 进阶分析的核心维度
- ``one_lot_winning_rate NUMERIC(8,6)`` — 一手中签率 (HK 专用; 0~1, 精度 0.0001%);
  A 股不分手, 留 NULL; FE-S4-002 雷达图五维之一
- ``oversubscribe_multiple NUMERIC(10,2)`` — 公开认购超额倍数 (HK 专用; 如 285.6
  = 285.6 倍, 极端值 5000+ 用 (10,2)); A 股留 NULL; FE-S4-002 雷达图五维之一

新加索引 (3 个, 匹配 BE-S4-003 list_historical / peer-aggregate 排序场景)
========================================================================
- ``ix_ipos_first_day_change`` (DESC NULLS LAST) — "热门排序" 场景:
  ``GET /ipos/historical?sort_by=first_day_change_pct DESC`` 主路径; NULLS LAST
  让上市前 IPO 不污染历史榜首
- ``ix_ipos_industry_year`` ON ``(industry_l1, EXTRACT(year FROM listing_date))``
  — 行业聚合: ``WHERE industry_l1='互联网' AND EXTRACT(year ...)=2024``;
  AI 规律分析候选池采样路径; B-tree 复合即可, 行业基数不大
- ``ix_ipos_status_listing_date`` PARTIAL ``WHERE status='listed'`` ON
  ``(status, listing_date DESC)`` — 历史 listed IPO 时间序列; partial 过滤掉
  upcoming/subscribing/withdrawn, 索引仅含真历史 IPO, 体积小 + 命中率高

不在本 PR (Sprint 5+ 视情况再加)
================================
- ``industry_aggregate_cache JSONB``: spec/11 §BE-S4-003 中走 Redis 缓存方案
  (`@cached(namespace='ipo:peer', ttl=600)`), 不落库; 真要做物化视图也排 Sprint 5
- K 线相关字段 (close_price_5d / close_price_30d / max_drawdown): spec/11 §S4 P1
  后置, MVP 不需要

回滚策略
========
``downgrade()``: 先 DROP INDEX 再 DROP COLUMN; 老数据零损 (新列全 nullable).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_ipos_historical"
down_revision: str | None = "0007_vip"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 3 个新列 ---
    op.add_column(
        "ipos",
        sa.Column(
            "first_day_change_pct",
            sa.Numeric(8, 4),
            nullable=True,
            comment="上市首日涨跌幅 % (HK/A 通用; 范围 [-100, 5000])",
        ),
    )
    op.add_column(
        "ipos",
        sa.Column(
            "one_lot_winning_rate",
            sa.Numeric(8, 6),
            nullable=True,
            comment="一手中签率 (HK 专用; 范围 [0, 1]; A 股 NULL)",
        ),
    )
    op.add_column(
        "ipos",
        sa.Column(
            "oversubscribe_multiple",
            sa.Numeric(10, 2),
            nullable=True,
            comment="公开认购超额倍数 (HK 专用; 285.6 = 285.6 倍; A 股 NULL)",
        ),
    )

    # --- 3 个新索引 ---
    # 1. 热门排序: first_day_change_pct DESC NULLS LAST
    op.execute(
        """
        CREATE INDEX ix_ipos_first_day_change
        ON ipos (first_day_change_pct DESC NULLS LAST);
        """
    )
    # 2. 行业 + 年份聚合 (AI 规律分析候选池)
    op.execute(
        """
        CREATE INDEX ix_ipos_industry_year
        ON ipos (industry_l1, EXTRACT(YEAR FROM listing_date));
        """
    )
    # 3. partial: 仅 listed 状态参与历史 IPO 检索
    op.execute(
        """
        CREATE INDEX ix_ipos_status_listing_date
        ON ipos (status, listing_date DESC)
        WHERE status = 'listed';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ipos_status_listing_date;")
    op.execute("DROP INDEX IF EXISTS ix_ipos_industry_year;")
    op.execute("DROP INDEX IF EXISTS ix_ipos_first_day_change;")

    op.drop_column("ipos", "oversubscribe_multiple")
    op.drop_column("ipos", "one_lot_winning_rate")
    op.drop_column("ipos", "first_day_change_pct")
