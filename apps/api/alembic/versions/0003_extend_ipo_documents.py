"""extend ipo_documents for RAG ingest pipeline (Sprint 2 BE-S2-003).

Revision ID: 0003_chunks
Revises: 0002_chat
Create Date: 2026-04-26

背景
====
``ipo_documents`` 表在 0001_init 已建好基本骨架 (UUID PK + ipo_id FK CASCADE +
``embedding vector(1024)`` + HNSW cosine 索引)。Sprint 1 没真写入数据。
本 PR (BE-S2-003) 只为 BE-S2-004 招股书入库 / BE-S2-005 混合检索补 6 列 + 2 索引,
不动已有结构, 也不动 HNSW 索引。

新加列 (全部 ALTER TABLE ADD COLUMN)
=====================================
- ``chunk_index INTEGER`` —— 同 ``doc_id`` 内 chunk 顺序号 (0-based);
  取相邻上下文 (chunk_index ± 1) / 招股书原文回显时排序用
- ``token_count INTEGER`` —— BAAI/bge-m3 tokenizer 算的 token 数;
  cost 调试 / chunk 长度直方图统计 / 限流分桶用
- ``content_hash CHAR(64)`` —— sha256(text) 16 进制; 让 BE-S2-004 直接走
  ``ON CONFLICT (doc_id, content_hash) DO NOTHING`` 防重灌
- ``embedding_model VARCHAR(64) NOT NULL DEFAULT 'BAAI/bge-m3'`` —— 多版本
  向量共存 (将来切 bge-m4 时新数据写新模型, 老 chunk 可异步回填)
- ``embedding_dim INTEGER NOT NULL DEFAULT 1024`` —— 元数据冗余, 让 SELECT
  阶段拒识维度不匹配的索引污染数据
- ``lang VARCHAR(8) NOT NULL DEFAULT 'zh'`` —— HK 招股书是英文 ('en'),
  A 股是中文 ('zh'); 后续 BE-S2-005 全文检索分词器路由用

新加索引
========
- ``uq_ipo_documents_doc_id_content_hash`` (UNIQUE, PARTIAL ``WHERE
  content_hash IS NOT NULL``) —— 老行 content_hash 是 NULL 不会被卡住,
  新行强一致防重
- ``ix_ipo_documents_doc_id_chunk_index`` (PARTIAL ``WHERE chunk_index IS
  NOT NULL``) —— 取相邻 chunk 上下文 / 拼回原文用; partial 避免空指针写
  也建索引浪费空间

不在本 PR (BE-S2-005 时再加 0004)
==================================
- ``tsv tsvector`` 列 + GIN 索引: 中文分词器选型 (zhparser / pg_trgm /
  应用层 rank-bm25) 还没敲定, BE-S2-005 真做混合检索时再决策
- HNSW ``ef_search`` runtime 参数: 不归 schema, 走 SET LOCAL 应用层

回滚策略
========
``downgrade()`` 反向, 先 DROP INDEX 再 DROP COLUMN; 老数据零损 (本 PR 加列
都允许 NULL 或带 DEFAULT)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_chunks"
down_revision: str | None = "0002_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 6 个新列 ---
    op.add_column(
        "ipo_documents",
        sa.Column(
            "chunk_index",
            sa.Integer(),
            nullable=True,
            comment="同 doc_id 内 chunk 顺序号 (0-based); 取上下文 ± 1 / 排序用",
        ),
    )
    op.add_column(
        "ipo_documents",
        sa.Column(
            "token_count",
            sa.Integer(),
            nullable=True,
            comment="bge-m3 tokenizer 算的 token 数; cost 调试用",
        ),
    )
    op.add_column(
        "ipo_documents",
        sa.Column(
            "content_hash",
            sa.CHAR(64),
            nullable=True,
            comment="sha256(text) 16 进制; 防同一 chunk 反复入库",
        ),
    )
    op.add_column(
        "ipo_documents",
        sa.Column(
            "embedding_model",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'BAAI/bge-m3'"),
            comment="多版本向量共存; 切模型时新数据写新值",
        ),
    )
    op.add_column(
        "ipo_documents",
        sa.Column(
            "embedding_dim",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1024"),
            comment="元数据冗余, 拒识维度不匹配的索引污染",
        ),
    )
    op.add_column(
        "ipo_documents",
        sa.Column(
            "lang",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'zh'"),
            comment="zh / en; HK 招股书走 en, A 股走 zh",
        ),
    )

    # --- 2 个新索引 (partial; 老行 NULL 不卡住) ---
    op.execute(
        """
        CREATE UNIQUE INDEX uq_ipo_documents_doc_id_content_hash
        ON ipo_documents (doc_id, content_hash)
        WHERE content_hash IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX ix_ipo_documents_doc_id_chunk_index
        ON ipo_documents (doc_id, chunk_index)
        WHERE chunk_index IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ipo_documents_doc_id_chunk_index;")
    op.execute("DROP INDEX IF EXISTS uq_ipo_documents_doc_id_content_hash;")

    op.drop_column("ipo_documents", "lang")
    op.drop_column("ipo_documents", "embedding_dim")
    op.drop_column("ipo_documents", "embedding_model")
    op.drop_column("ipo_documents", "content_hash")
    op.drop_column("ipo_documents", "token_count")
    op.drop_column("ipo_documents", "chunk_index")
