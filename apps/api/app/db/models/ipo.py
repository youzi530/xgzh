"""IPO 域 ORM: IPO 主表 + IPODocument (RAG chunk).

IPODocument 即 spec/05 的 ``rag_chunks``, 重命名为 ``ipo_documents`` 以
更贴合业务语义 (Sprint 1 范围内只支持 IPO 招股书 chunk; 文章/历史报告
chunk 在 Sprint 2 引入时若发现需要拆分, 再单独建 ``article_chunks`` 表)。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin


class IPO(Base, TimestampMixin):
    """新股主表（同时支持 HK / A / US）."""

    __tablename__ = "ipos"
    __table_args__ = (
        UniqueConstraint("code", "market", name="uq_ipos_code_market"),
        Index("ix_ipos_status", "status"),
        Index("ix_ipos_listing_date", "listing_date"),
        Index("ix_ipos_subscribe_window", "subscribe_start", "subscribe_end"),
    )

    ipo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str] = mapped_column(String(4), nullable=False, comment="HK/A/US")
    industry_l1: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry_l2: Mapped[str | None] = mapped_column(String(64), nullable=True)

    issue_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    # BUG-S6.8-004: 招股价区间 (港股 ipolist 50/50 行都是 "x-y" 格式).
    # ``price_max == legacy issue_price`` (升限价, 历史 raised_amount 计算口径);
    # FE 检测 ``price_min != price_max`` 显示区间, 否则显示单值.
    price_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    price_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    issue_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    subscribe_start: Mapped[datetime | None] = mapped_column(nullable=True)
    subscribe_end: Mapped[datetime | None] = mapped_column(nullable=True)
    raised_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    pe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)

    # Sprint 4 BE-S4-001: 历史 IPO 数据沉淀 — 上市后回填字段, upcoming/subscribing 阶段为 NULL
    first_day_change_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4),
        nullable=True,
        comment="上市首日涨跌幅 % (HK/A 通用; 范围 [-100, 5000])",
    )
    one_lot_winning_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 6),
        nullable=True,
        comment="一手中签率 (HK 专用; 范围 [0, 1]; A 股 NULL)",
    )
    oversubscribe_multiple: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
        comment="公开认购超额倍数 (HK 专用; 285.6 = 285.6 倍; A 股 NULL)",
    )

    sponsors: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    underwriters: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    prospectus_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment="upcoming/subscribing/listed/withdrawn",
    )
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    data_source: Mapped[str | None] = mapped_column(String(32), nullable=True)


class IPODocument(Base, TimestampMixin):
    """IPO 招股书 / 文档 chunk, 用于 RAG 检索.

    - ``embedding`` 维度对齐 ``embedding_model`` (默认 bge-m3 → 1024); 多版本
      共存时按 ``embedding_model`` 列分流, 避免索引污染.
    - ``chunk_index`` / ``content_hash`` (BE-S2-003): 让 BE-S2-004 入库流水线
      支持有序切分 + 幂等去重 (同 ``(doc_id, content_hash)`` 直接 ON CONFLICT
      DO NOTHING).
    - HNSW (cosine) + ``(doc_id, content_hash)`` partial unique + ``(doc_id,
      chunk_index)`` partial 共 3 个 RAG 路径核心索引.
    """

    __tablename__ = "ipo_documents"
    __table_args__ = (
        Index("ix_ipo_documents_ipo_code_doc_type", "ipo_code", "doc_type"),
        Index("ix_ipo_documents_doc_id", "doc_id"),
        Index(
            "ix_ipo_documents_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # 0003: partial 索引 (NULL 不入索引), 老 Sprint 1 行不会被卡住
        Index(
            "uq_ipo_documents_doc_id_content_hash",
            "doc_id",
            "content_hash",
            unique=True,
            postgresql_where=text("content_hash IS NOT NULL"),
        ),
        Index(
            "ix_ipo_documents_doc_id_chunk_index",
            "doc_id",
            "chunk_index",
            postgresql_where=text("chunk_index IS NOT NULL"),
        ),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    ipo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ipos.ipo_id", ondelete="CASCADE"),
        nullable=True,
    )
    ipo_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="原始文档 ID/hash")
    doc_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="prospectus/financial/article/history",
    )
    section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_content: Mapped[str] = mapped_column("text", Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    doc_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)

    # ─── BE-S2-003 新增 (Sprint 2 RAG 流水线必需) ────────────────────
    chunk_index: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="同 doc_id 内 chunk 顺序号 (0-based); 取上下文 ± 1 / 排序用",
    )
    token_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="bge-m3 tokenizer 算的 token 数; cost 调试 / 长度直方图用",
    )
    content_hash: Mapped[str | None] = mapped_column(
        CHAR(64),
        nullable=True,
        comment="sha256(text) 16 进制; (doc_id, content_hash) partial unique 防重",
    )
    embedding_model: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'BAAI/bge-m3'"),
        comment="多版本向量共存; 切模型时新数据写新值",
    )
    embedding_dim: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1024"),
        comment="元数据冗余, 拒识维度不匹配的索引污染",
    )
    lang: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        server_default=text("'zh'"),
        comment="zh / en; HK 招股书走 en, A 股走 zh",
    )
