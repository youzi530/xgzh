"""add article tables (Sprint 3 BE-S3-001):
articles + article_topics + tsv generated column + 5 indexes.

Revision ID: 0005_articles
Revises: 0004_fts
Create Date: 2026-04-27

背景
====
BE-S3-001 = Sprint 3 内容侧三条线 (文章 / 券商 / VIP) 的第一张地基:
后续 BE-S3-002 (多源 ingest) / BE-S3-003 (simhash 去重) /
BE-S3-004 (情感打标) / BE-S3-005 (TL;DR) / BE-S3-006 (列表搜索) 全部在
这两张表上读写, 一次落定.

设计要点
========
1. **schema 沿用 Sprint 1/2 命名约定** (``ix_*`` 前缀, ``fk_*_<col>_<reftable>``).
   spec/10 §BE-S3-001 写的是 ``idx_articles_*``, 但全项目其它表都用 ``ix_*``,
   保持索引前缀一致比对齐 spec 字面值更重要; 已在实施总结里回填修订过 spec.

2. **``tsv`` 生成列与 BE-S2-005 (0004) ``ipo_documents.tsv`` 同款**:
   - 来源: ``coalesce(title,'') || ' ' || coalesce(summary,'')``
   - text-search config: ``simple`` (PG 内置, 不上 zhparser; 0004 已论证)
   - 中文字符级预切: regex ``[\u4e00-\u9fff]`` 后插空格, 让"招股说明书"切 5 个 token
   - 全项目中文搜索单一路径, 与 ``hybrid_search`` 写法对齐, 维护点收敛

3. **``simhash`` ``BYTEA(8)`` + CHECK ``octet_length = 8 OR NULL``**:
   PG ``bytea`` 不限长度, ``LargeBinary(length=8)`` 是 schema 提示但不强制.
   显式 CHECK 约束兜底, 防止 ingest 写到 9/10 字节 simhash 污染索引选择性.

4. **``related_ipos`` GIN 索引**: 让 ``related_ipos @> '[{"code":"00700.HK"}]'``
   (BE-S3-006 按 IPO 反查相关文章) 走索引, 不全表扫.

5. **``articles.original_url`` UNIQUE**: 写入端去重核心约束, BE-S3-002
   dispatcher 走 ``INSERT ... ON CONFLICT (original_url) DO NOTHING``
   实现幂等抓取 (同 URL 反复跑 ingest 不会插重).

6. **``article_topics`` 双 CASCADE**:
   parent / child 任一被删, 主题映射立即失效, 保留无意义 (与 ``ipo_documents``
   ``ON DELETE CASCADE`` 思路一致).

回滚策略
========
``downgrade()`` 反向: 先 drop ``article_topics`` (依赖 ``articles``), 再 drop
``articles``. tsv generated 列随 ``DROP TABLE`` 一起消失, 不需要单独
``ALTER ... DROP COLUMN``. 索引同样随表 drop.

测试覆盖
========
``tests/integration/test_article_tables.py`` ≥ 8 条 (BE-S3-001 AC §4):
- schema 形状 (列数 / 类型 / 约束)
- UNIQUE / FK CASCADE / CHECK 约束生效
- tsv generated 列自动填 + 中文字符级切
- GIN 索引 plainto_tsquery / @> 命中
- downgrade idempotent
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0005_articles"
down_revision: str | None = "0004_fts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 与 0004 ``ipo_documents.tsv`` 同款 CJK 预切 SQL — 单一真相, 中文搜索全项目对齐.
# 写入端 (本 migration) 走 ``coalesce(title,'') || ' ' || coalesce(summary,'')``,
# 查询端 (BE-S3-006 后续) 走 ``plainto_tsquery('simple', regex_replace(query))``.
_CJK_PRESPLIT_SQL = (
    r"regexp_replace("
    r"  coalesce(title,'') || ' ' || coalesce(summary,''), "
    r"  E'([\u4e00-\u9fff])', E'\\1 ', 'g'"
    r")"
)


def upgrade() -> None:
    # --- articles ---
    op.create_table(
        "articles",
        sa.Column(
            "article_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column(
            "summary",
            sa.Text,
            nullable=True,
            comment="100 字 AI 摘要; BE-S3-004/005 后填",
        ),
        sa.Column(
            "source_name",
            sa.String(64),
            nullable=False,
            comment="数据源名, 如 '雪球' / '智通财经'",
        ),
        sa.Column("source_logo_url", sa.Text, nullable=True),
        sa.Column(
            "source_credibility",
            sa.SmallInteger,
            nullable=False,
            server_default=sa.text("2"),
            comment="1=低 / 2=中 / 3=高 公信力评级",
        ),
        sa.Column(
            "original_url",
            sa.Text,
            nullable=False,
            comment="原文 URL; UNIQUE 防同源重复入库",
        ),
        sa.Column(
            "market",
            sa.String(8),
            nullable=False,
            comment="HK / A / BOTH",
        ),
        sa.Column(
            "related_ipos",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="[{code, market, name}, ...]",
        ),
        sa.Column(
            "sentiment",
            sa.String(16),
            nullable=True,
            comment="bullish / neutral / bearish; NULL = 还未打标",
        ),
        sa.Column(
            "sentiment_score",
            sa.Numeric(4, 3),
            nullable=True,
            comment="情感置信度 -1.000 ~ 1.000",
        ),
        sa.Column(
            "keywords",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="3-5 个关键词; BE-S3-004 抽取",
        ),
        sa.Column(
            "simhash",
            sa.LargeBinary(length=8),
            nullable=True,
            comment="64 bit simhash 定长 BYTEA; NULL = 还没算",
        ),
        sa.Column(
            "hot_score",
            sa.Numeric(8, 2),
            nullable=False,
            server_default=sa.text("0"),
            comment="热度排序 (点赞 + 评论加权)",
        ),
        sa.Column(
            "is_full_text_available",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
            comment="版权合规字段; false = 仅展示摘要 + 跳转外链",
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="原始发布时间 (来自源)",
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="入库时间",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("original_url", name="uq_articles_original_url"),
        sa.CheckConstraint(
            "octet_length(simhash) = 8 OR simhash IS NULL",
            name="ck_articles_simhash_8bytes",
        ),
    )

    # --- articles 普通二级索引 (3 个 B-tree) ---
    op.execute(
        """
        CREATE INDEX ix_articles_market_published_at_desc
        ON articles (market, published_at DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX ix_articles_sentiment_published_at_desc
        ON articles (sentiment, published_at DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX ix_articles_source_published_at_desc
        ON articles (source_name, published_at DESC);
        """
    )

    # --- articles GIN 索引 (related_ipos JSONB containment) ---
    op.execute(
        """
        CREATE INDEX ix_articles_related_ipos_gin
        ON articles USING GIN (related_ipos);
        """
    )

    # --- articles tsv 生成列 + GIN 索引 (与 BE-S2-005 同款) ---
    op.execute(
        f"""
        ALTER TABLE articles
        ADD COLUMN tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('simple', {_CJK_PRESPLIT_SQL})
        ) STORED;
        """
    )
    op.execute(
        """
        CREATE INDEX ix_articles_tsv_gin
        ON articles USING GIN (tsv);
        """
    )

    # --- article_topics ---
    op.create_table(
        "article_topics",
        sa.Column(
            "topic_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "parent_article_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "articles.article_id",
                ondelete="CASCADE",
                name="fk_article_topics_parent_article_id_articles",
            ),
            nullable=False,
            comment="主文 article_id",
        ),
        sa.Column(
            "child_article_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "articles.article_id",
                ondelete="CASCADE",
                name="fk_article_topics_child_article_id_articles",
            ),
            nullable=False,
            comment="子文 article_id; UNIQUE 保证子文唯一归属",
        ),
        sa.Column(
            "simhash_distance",
            sa.SmallInteger,
            nullable=True,
            comment="海明距离 (0-64); 调试 / 阈值回放用",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "child_article_id",
            name="uq_article_topics_child_article_id",
        ),
    )
    op.create_index(
        "ix_article_topics_parent_article_id",
        "article_topics",
        ["parent_article_id"],
    )


def downgrade() -> None:
    # 反向顺序: 叶子表 (依赖 articles) 先删, 主表后删. 索引 / tsv 列随表 drop.
    op.drop_table("article_topics")
    op.execute("DROP INDEX IF EXISTS ix_articles_tsv_gin;")
    op.execute("DROP INDEX IF EXISTS ix_articles_related_ipos_gin;")
    op.execute("DROP INDEX IF EXISTS ix_articles_source_published_at_desc;")
    op.execute("DROP INDEX IF EXISTS ix_articles_sentiment_published_at_desc;")
    op.execute("DROP INDEX IF EXISTS ix_articles_market_published_at_desc;")
    op.drop_table("articles")
