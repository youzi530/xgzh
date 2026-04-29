"""知识库 ORM (Sprint 6 BE-S6-004): KnowledgeArticle.

spec/13 §主线 C — 30 篇 curated markdown 入库, 列表 + 详情 + 分类筛选.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin


class KnowledgeArticle(Base, TimestampMixin):
    """知识库一篇文章.

    继承 ``TimestampMixin``: ``updated_at`` 跟踪内容修订 (admin 修订 / FAQ 增补).
    """

    __tablename__ = "knowledge_articles"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_knowledge_slug"),
        CheckConstraint(
            "category IN ('hk', 'cn', 'general')",
            name="ck_knowledge_category",
        ),
        CheckConstraint("level >= 1 AND level <= 3", name="ck_knowledge_level"),
        CheckConstraint(
            "source IN ('curated', 'crawled', 'ai-generated')",
            name="ck_knowledge_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    slug: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="URL-friendly key, e.g. 'hk-subscription-key-dates'",
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="hk / cn / general",
    )
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text()), nullable=True)
    level: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        server_default=text("1"),
        comment="1=入门 / 2=进阶 / 3=实战",
    )
    content_md: Mapped[str] = mapped_column(Text(), nullable=False)
    toc_json: Mapped[Any | None] = mapped_column(
        JSONB(),
        nullable=True,
        comment="目录数组 [{level, text, anchor}, ...]",
    )
    view_count: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        server_default=text("0"),
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("true"),
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'curated'"),
        comment="curated / crawled / ai-generated",
    )
    source_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    legal_disclaimer: Mapped[str | None] = mapped_column(Text(), nullable=True)
    # created_at / updated_at 来自 TimestampMixin
