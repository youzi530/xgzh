"""Article 域 ORM (BE-S3-001): 文章主表 + 同主题去重映射.

Article 是 Sprint 3 内容侧的核心: 多源 ingest (BE-S3-002) → simhash 去重
(BE-S3-003) → 情感 / TL;DR (BE-S3-004/005) → 列表 / 详情 / 全局搜索
(BE-S3-006) 全部读写这两张表.

总体设计
========
- 主键 ``article_id`` / ``topic_id`` UUID, ``gen_random_uuid()`` (复用 0001 已建
  的 pgcrypto 扩展; 与 ``users`` / ``ipos`` / ``chat_*`` 风格一致).
- 时间戳一律 ``TIMESTAMPTZ NOT NULL``, ``server_default=now()``.
- 枚举字段 (``market`` / ``sentiment``) 一律 ``String + comment + Python Literal``,
  不用 PG ENUM. 同 ``ipos.status`` / ``chat_messages.role`` 方案, 加值无需
  ``ALTER TYPE``.
- 写入端去重: ``original_url`` 加 UNIQUE, BE-S3-002 dispatcher 走
  ``INSERT ... ON CONFLICT (original_url) DO NOTHING`` 实现幂等抓取.
- 全文搜索: ``tsv`` 生成列, 与 BE-S2-005 (``ipo_documents.tsv``) 同款 ``simple``
  config + CJK 字符级预切, 单一中文搜索路径全项目对齐.

外键级联策略
============
- ``article_topics.parent_article_id`` → ``articles.article_id`` ``CASCADE``
- ``article_topics.child_article_id``  → ``articles.article_id`` ``CASCADE``
  双 CASCADE: 父 / 子文任一被删, 主题映射立即失效, 保留无意义.
  与 ``ipo_documents`` / ``chat_*`` 的 CASCADE 思路一致.

索引设计 (5 个二级索引, spec/10 §BE-S3-001 锁定)
================================================
- ``articles(market, published_at DESC)``        — 列表分页主索引 (FE 切 HK / A tab)
- ``articles(sentiment, published_at DESC)``     — 情感筛选 (FE 多空热度榜)
- ``articles(source_name, published_at DESC)``   — 来源筛选 (运营审核)
- ``articles`` GIN on ``related_ipos``           — 按 IPO code 反查相关文章
- ``articles`` GIN on ``tsv``                    — BM25 全文搜索 (BE-S3-006)

命名沿用 ``ix_*`` 前缀 (Sprint 1/2 全部表), 与 spec/10 写的 ``idx_*`` 略偏差
但保持项目一致性优先 — 改 spec 比改全表代价低. 已在 spec/10 实施总结里回填.

simhash 列存储
==============
``BYTEA(8)`` 64 bit 定长 simhash, NULL = 还没算 (BE-S3-003 异步补).
PG 的 ``bytea`` 不限长度, 这里加 ``CheckConstraint('octet_length(simhash) = 8
OR simhash IS NULL')`` 锁死, 避免误存其他长度的二进制.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin


class Article(Base, TimestampMixin):
    """文章主表 (财经资讯 / 公告 / 解读).

    数据流: BE-S3-002 ingest 写入基础字段 (title / summary 留空 / source_*
    / original_url / market / published_at), BE-S3-003 异步补 ``simhash``,
    BE-S3-004 异步补 ``sentiment`` / ``sentiment_score`` / ``keywords``,
    BE-S3-005 按需生成 ``summary`` (TL;DR 缓存到 Redis, 兜底落 PG).

    版权合规: ``is_full_text_available`` 占位, 部分来源仅授权摘要 + 原文跳转
    (例: 智通 RSS 给摘要不给全文), FE 据此决定渲染"全文 / 跳转外部"按钮.
    """

    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("original_url", name="uq_articles_original_url"),
        CheckConstraint(
            "octet_length(simhash) = 8 OR simhash IS NULL",
            name="ck_articles_simhash_8bytes",
        ),
        # 列表分页主索引 (FE 按市场 tab 切): WHERE market = ? ORDER BY published_at DESC
        Index(
            "ix_articles_market_published_at_desc",
            "market",
            text("published_at DESC"),
        ),
        # 情感筛选: WHERE sentiment = 'bullish' ORDER BY published_at DESC
        Index(
            "ix_articles_sentiment_published_at_desc",
            "sentiment",
            text("published_at DESC"),
        ),
        # 来源筛选 (运营审核 / 用户屏蔽某源)
        Index(
            "ix_articles_source_published_at_desc",
            "source_name",
            text("published_at DESC"),
        ),
        # JSONB containment: WHERE related_ipos @> '[{"code":"00700.HK"}]'
        Index(
            "ix_articles_related_ipos_gin",
            "related_ipos",
            postgresql_using="gin",
        ),
        # 注: ``ix_articles_tsv_gin`` 不在 ORM 里声明 — tsv 是 PG GENERATED 列, ORM
        # 不感知; 索引由 alembic 0005 raw SQL ``CREATE INDEX ... USING GIN (tsv)``
        # 创建. 与 ``ipo_documents.ix_ipo_documents_tsv`` (BE-S2-005) 同方案.
    )

    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="100 字 AI 摘要; BE-S3-004/005 后填",
    )

    source_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="数据源名, 如 '雪球' / '智通财经'",
    )
    source_logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_credibility: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("2"),
        comment="1=低 / 2=中 / 3=高 公信力评级 (运营维护)",
    )
    original_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="原文 URL; UNIQUE 防同源重复入库",
    )

    market: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="HK / A / BOTH (跨市场议题)",
    )
    related_ipos: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="[{code, market, name}, ...]; 文章关联的 IPO; GIN 索引支持 @> 查",
    )

    sentiment: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment="bullish / neutral / bearish; NULL = 还未打标 (BE-S3-004 后补)",
    )
    sentiment_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3),
        nullable=True,
        comment="情感置信度 -1.000 ~ 1.000; NULL = 还未打标",
    )
    keywords: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="3-5 个关键词数组; BE-S3-004 LLM 抽取",
    )

    simhash: Mapped[bytes | None] = mapped_column(
        LargeBinary(length=8),
        nullable=True,
        comment="64 bit simhash 定长 BYTEA; NULL = 还没算 (BE-S3-003 后补)",
    )

    hot_score: Mapped[Decimal] = mapped_column(
        Numeric(8, 2),
        nullable=False,
        server_default=text("0"),
        comment="热度排序 (点赞 + 评论加权); BE-S3-002 基础值, 后续可累加",
    )
    is_full_text_available: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="版权合规字段; false = FE 仅展示摘要 + 跳转外链",
    )

    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="原始发布时间 (来自源, 不是入库时间)",
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="入库时间 (审计 / 监控 ingest 延迟)",
    )

    # ``tsv`` 是 PG GENERATED 列 (GENERATED ALWAYS AS ... STORED), alembic 0005 用
    # raw SQL 创建. **ORM 故意不声明此列** — 与 ``IPODocument`` (BE-S2-005) 同方案:
    # 1. SQLAlchemy 没内置 TSVECTOR 类型, 用 Text 占位会让 INSERT 误带 NULL ::VARCHAR,
    #    触发 ``DatatypeMismatchError: column "tsv" is of type tsvector but expression
    #    is of type character varying``.
    # 2. ``Computed()`` 也不行: PG GENERATED 表达式含 ``regexp_replace`` /
    #    ``to_tsvector`` 复合函数, SQLAlchemy 反向 autogenerate 会把它误同步成
    #    简单 ``GENERATED ALWAYS AS (col) STORED``.
    # 3. BE-S3-006 列表搜索查询走 raw SQL: ``text("tsv @@ plainto_tsquery(...)")``,
    #    ORM 列不需要, 与 ``hybrid_search`` 写法对齐.


class ArticleTopic(Base):
    """同主题文章去重映射 (BE-S3-003 simhash 64 bit + 海明距离判同主题).

    简化的并查集 (parent / child 两列), 不严格规范化为 set:
    - 主文 ``parent_article_id`` = 该主题最早 / 公信力最高的版本
    - 子文 ``child_article_id`` = 后续相似度高 (海明距离 ≤ 3) 的复刊 / 转发版本

    业务读路径:
    - 文章列表只展示 parent (排除子文): ``WHERE article_id NOT IN (SELECT
      child_article_id FROM article_topics)``
    - 详情页显示 "本主题相关 N 篇" 时反查: ``SELECT article_id WHERE article_id =
      <topic.parent> OR article_id IN (子文)``

    不带 TimestampMixin: 这是只在写入端记一次的去重映射, 不需要 updated_at;
    debug 用 ``simhash_distance`` + ``created_at`` 即足够回放当时的判同主题决策.
    """

    __tablename__ = "article_topics"
    __table_args__ = (
        # child 唯一: 同一篇文章不应同时是多个主题的子文
        UniqueConstraint(
            "child_article_id",
            name="uq_article_topics_child_article_id",
        ),
        Index(
            "ix_article_topics_parent_article_id",
            "parent_article_id",
        ),
    )

    topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    parent_article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "articles.article_id",
            ondelete="CASCADE",
            name="fk_article_topics_parent_article_id_articles",
        ),
        nullable=False,
        comment="主文 article_id; 删父文 → 整组主题映射 CASCADE 清",
    )
    child_article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "articles.article_id",
            ondelete="CASCADE",
            name="fk_article_topics_child_article_id_articles",
        ),
        nullable=False,
        comment="子文 article_id; UNIQUE 保证子文唯一归属",
    )
    simhash_distance: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
        comment="海明距离 (0-64); 调试 / 阈值回放用; NULL = 历史数据没记",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
