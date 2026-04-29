"""create knowledge_articles table (Sprint 6 BE-S6-004): 知识库 curated 内容.

Revision ID: 0013_knowledge_articles
Revises: 0012_subscriptions
Create Date: 2026-04-29

背景
====
spec/13 §主线 C - 港 / A 股打新知识库. 30 篇 curated markdown 入库, 提供分类
chip + 详情 markdown 渲染. spike-2 结论是不爬虫 (反爬 + 版权), 自己写 + LLM 辅助.

字段
====
- ``slug VARCHAR(64) UNIQUE`` URL-friendly key (e.g. 'hk-subscription-key-dates'),
  FE 路由 ``/knowledge/:slug`` 直接用. 不暴露内部 UUID.
- ``title / content_md`` 文章标题 + markdown 原文 (UTF-8). 不存 HTML 让 FE 自渲染.
- ``category CHAR(8)`` 'hk' / 'cn' / 'general' (CHECK constraint). 主分类 chip.
- ``tags TEXT[]`` PG 数组类型, 例如 ``['入门', '日期', '基础']``; FE 二级筛选用.
  PG ARRAY 上 GIN 索引开销大, MVP 不上, 全表扫即可 (30 行 + 半年增长 < 100 行).
- ``level INTEGER DEFAULT 1`` 1=入门 / 2=进阶 / 3=实战; 复合排序用.
- ``toc_json JSONB`` markdown 目录, FE 渲染锚点 (``[{"level":2, "text":"...", "anchor":"..."}]``).
  入库时由 import 脚本提取 H2/H3 自动生成.
- ``view_count INTEGER DEFAULT 0`` 详情接口异步 +1 (FastAPI BackgroundTasks).
- ``is_published BOOLEAN`` 软下架开关. partial 索引: 列表只查 published.
- ``source VARCHAR(32)`` 'curated' / 'crawled' / 'ai-generated' — 法务审计用.
- ``source_url / legal_disclaimer`` 引用第三方时律师文案 — 防版权诉讼.

索引
====
- ``ix_knowledge_category_level (category, level)`` — 列表按分类筛 + level 排序
- ``ix_knowledge_published`` partial WHERE is_published=TRUE — 列表全表扫前先 partial filter
- ``uq_knowledge_slug`` UNIQUE(slug) — 详情 lookup + import 防重复

回滚
====
DROP TABLE; 全部 30 篇内容丢. import 脚本可重新跑回填 (idempotent on slug).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_knowledge_articles"
down_revision: str | None = "0012_subscriptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_articles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column(
            "category",
            sa.String(8),
            nullable=False,
            comment="hk / cn / general",
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
            comment="二级筛选标签数组, e.g. ['入门', '日期']",
        ),
        sa.Column(
            "level",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="1=入门 / 2=进阶 / 3=实战",
        ),
        sa.Column("content_md", sa.Text(), nullable=False, comment="markdown 正文"),
        sa.Column(
            "toc_json",
            postgresql.JSONB(),
            nullable=True,
            comment="目录, FE 锚点用; import 脚本生成",
        ),
        sa.Column(
            "view_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "is_published",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'curated'"),
            comment="curated / crawled / ai-generated",
        ),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "legal_disclaimer",
            sa.Text(),
            nullable=True,
            comment="引用第三方时律师文案",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("slug", name="uq_knowledge_slug"),
        sa.CheckConstraint(
            "category IN ('hk', 'cn', 'general')",
            name="ck_knowledge_category",
        ),
        sa.CheckConstraint(
            "level >= 1 AND level <= 3",
            name="ck_knowledge_level",
        ),
        sa.CheckConstraint(
            "source IN ('curated', 'crawled', 'ai-generated')",
            name="ck_knowledge_source",
        ),
    )
    op.execute(
        "CREATE INDEX ix_knowledge_category_level "
        "ON knowledge_articles (category, level);"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_published "
        "ON knowledge_articles (created_at DESC) WHERE is_published = TRUE;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_published;")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_category_level;")
    op.drop_table("knowledge_articles")
