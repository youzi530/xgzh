"""add tsvector full-text index to ipo_documents (Sprint 2 BE-S2-005).

Revision ID: 0004_fts
Revises: 0003_chunks
Create Date: 2026-04-26

背景
====
BE-S2-005 混合检索 = pgvector cosine (BE-S2-003 已落) + BM25 (本 PR) + RRF +
bge-reranker-v2-m3 重排. 本 PR 只补 BM25 那条路径要的 ``tsvector`` 生成列 + GIN
索引, 让 PG 内置 ``ts_rank_cd`` / ``@@`` 直接可用.

为什么用 ``simple`` config + 中文字符级预切, 不上 zhparser
=============================================================
zhparser (PostgreSQL 中文分词扩展) 的痛点:
  1. 装难: 需要 sudo make + scws 字典 + ALTER SYSTEM CREATE EXTENSION,
     CI 容器跑不了, 本地开发 docker 镜像也得自定义构建
  2. 字典维护: 招股书里的金融术语 ("绿鞋", "暗盘") / 公司简称 ("阿里" 切不切) 都
     需要手动加词, 投入产出不划算
  3. spec/04 / spec/06 已写"走精简包路线", 第三方 PG 扩展同样适用

替代方案: ``simple`` text-search config (PG 内置, 不做 stemming/字典) + 在写入端
把每个 CJK 字符后插一个空格. 效果:
  - 中文 → 字符级 token (例如 "招股说明书" 变 5 个独立 token: 招 / 股 / 说 / 明 / 书)
  - 英文 → 词级 token (空格切; ``simple`` 不做小写化, 但我们走 plainto_tsquery
    时 PG 会自动小写, 没影响)
  - 中英混排招股书全部走同一条路径

CJK 字符级 BM25 在中文搜索领域是常用 baseline (ElasticSearch ngram=1 / Solr
StandardTokenizer 也是这套), 召回率会高 / 精度会低, 但**RRF 融合 + reranker 二阶段
再排**就把精度补回来了, 这正是混合检索 / cross-encoder rerank 设计的初衷.

后续真发现 baseline 不够 (例如 BE-S2-009 评测召回@5 < 60%), 再考虑切 zhparser /
应用层 jieba+rank-bm25 备选.

设计选择
========
1. **GENERATED ALWAYS AS ... STORED** (PG 12+): 让 ``tsv`` 列在 INSERT/UPDATE
   ``text`` 时自动重算, 业务代码 0 改动 (写入路径不需要管 tsv); 老行 (BE-S2-004
   已落地) ALTER ADD COLUMN 时 PG 一次性回填
2. **regexp_replace pattern ``[\u4e00-\u9fff]``**: U+4E00 - U+9FFF 是 CJK 统一
   汉字基本区, 覆盖中日韩共用汉字 + 简繁体. 不覆盖扩展 A/B 区 (生僻字, 招股书几乎
   不会出现) 与全角标点 (有自己的 token, 不需要切)
3. **GIN 索引 + ``USING GIN``**: BM25 / phrase 检索标配. GiST 也可但更新成本高,
   只读多写少场景 GIN 全胜
4. **不在本 PR 加 ef_search runtime 参数 / hnsw 调优**: 那是检索代码层 SET LOCAL,
   不归 schema. spec/06 BE-S2-005 实施时再决定要不要 SET LOCAL hnsw.ef_search

回滚
====
``downgrade()`` 反向: DROP INDEX + DROP COLUMN. tsv 列被删时招股书原文 (text 列)
完全不受影响.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_fts"
down_revision: str | None = "0003_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 与 hybrid_search.py 保持单一真相: query 侧也用同样的 regex 预切
_CJK_PRESPLIT_SQL = (
    r"regexp_replace(text, E'([\u4e00-\u9fff])', E'\\1 ', 'g')"
)


def upgrade() -> None:
    # 1. 给 ipo_documents 加 tsv 生成列
    op.execute(
        f"""
        ALTER TABLE ipo_documents
        ADD COLUMN tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('simple', {_CJK_PRESPLIT_SQL})
        ) STORED;
        """
    )

    # 2. tsv 上建 GIN 索引 (BM25 ts_rank / @@ 走它)
    op.execute(
        """
        CREATE INDEX ix_ipo_documents_tsv
        ON ipo_documents USING GIN (tsv);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ipo_documents_tsv;")
    op.execute("ALTER TABLE ipo_documents DROP COLUMN IF EXISTS tsv;")
