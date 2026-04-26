"""混合检索 (BE-S2-005 RAG 主线最后一块拼图).

向量召回 (HNSW cosine) + BM25 召回 (PG ``tsvector`` + ``ts_rank_cd``)
→ Reciprocal Rank Fusion 融合 → bge-reranker-v2-m3 二阶段精排 → 返回 top-K.

为什么三阶段而不是单路检索
==========================
- **单路向量**: bge-m3 在金融术语 / 公司简称上召回稳定但精度天花板低 (cosine 距离
  ~0.6-0.8 的尾部样本噪声大)
- **单路 BM25**: 关键词强匹配召回率高但泛化弱 (问"业绩亮点"时找不到"经营成果")
- **Reciprocal Rank Fusion (Cormack et al. 2009)**: 用 ``score = Σ 1/(k + rank)``
  把两路 rank 加和; 不需要分数 normalize, 对两路尺度差异天然鲁棒, 是混合检索的事
  实标准
- **Cross-encoder rerank (bge-reranker-v2-m3)**: 把 query 与候选 chunk 拼起来过
  Transformer, 输出标量 relevance score; 比双塔 embedding 准但慢 50-100x, 所以
  只对 top-N (默认 20) 的小候选池跑

设计选择
========
1. **Vector / BM25 两路 SQL 分别打 (而非单 SQL UNION ALL)**: 各自走自家索引计划
   (HNSW vs GIN), planner 优化更友好; 应用层做融合也方便单测 mock
2. **过滤推到 SQL WHERE**: ``ipo_code`` / ``doc_type`` / ``lang`` / ``embedding_dim``
   全在两条 SQL 的 WHERE 里, 利用现有 ``(ipo_code, doc_type)`` btree 索引
3. **embedding 维度兜底**: 检索时强制 ``embedding_dim = settings.llm_embedding_dim``,
   防止异 dim 数据 (BE-S2-004 已防过, 此处再守一次) 被算 cosine 距离时 PG 报错
4. **CJK 字符级预切对齐 0004 migration**: 写入端用 ``regexp_replace([\u4e00-\u9fff])``
   生成 tsv, 查询端用同样的预处理喂 ``plainto_tsquery``, 否则中文 query token
   匹配不上字符级 tsv 的"招"/"股"
5. **空 BM25 query 兜底**: 当 query 全部是停用词 / 标点 (PG plainto_tsquery 返回空)
   时跳过 BM25 路径, 仅走 vector. spec/06 BE-S2-005 兜底策略
6. **rerank 失败 → 走 RRF 排序**: SiliconFlow API 抖动 / 配额耗尽时不让整条检索链
   断, 回退 RRF top-K. 单测 / CI 默认 ``rag_use_rerank=False`` 也走这条
7. **不在本 PR 做 query rewrite / HyDE**: spec/04 BE-S2-007 LangGraph 主循环里
   做 query 重写更合适 (那里有 LLM context); 本层只做单 query 检索
8. **不在本 PR 做语义缓存**: query 命中率低, 加缓存收益小; BE-S2-007 轮内 cache
   足够

stats
=====
- ``vector_hits`` / ``bm25_hits``: 两路实际召回数 (≤ top_k)
- ``fused_count``: RRF 后唯一 chunk 数 (vector + bm25 - 交集)
- ``reranked``: 是否走了 rerank (bool)
- ``elapsed_ms``: 端到端耗时 (vector + bm25 SQL + RRF + rerank)
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Float, Integer, String

from app.adapters.llm_client import LLMError, embed, rerank
from app.core.config import Settings, get_settings
from app.core.logging import logger

# 与 alembic 0004 一致的 CJK 字符级预切 (单一真相)
_CJK_CHAR_RE: Final[re.Pattern[str]] = re.compile(r"([\u4e00-\u9fff])")


def _cjk_presplit(s: str) -> str:
    """每个 CJK 字符后插一个空格, 让 simple tsvector / tsquery 按字切.

    与 0004 migration 的 ``regexp_replace(text, E'([\\u4e00-\\u9fff])', E'\\\\1 ', 'g')``
    完全等价. 单元测试覆盖.
    """
    return _CJK_CHAR_RE.sub(r"\1 ", s)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """混合检索单条结果. 字段命名贴 BE-S2-007 引用源装配的需求."""

    chunk_id: uuid.UUID
    doc_id: str
    ipo_code: str | None
    chunk_index: int | None
    page: int | None
    text: str
    score: float  # 最终分数 (rerank 后是 reranker score, 否则是 RRF score)
    rrf_score: float  # RRF 融合分 (诊断用; rerank 开时与 score 不一定一致)
    vector_rank: int | None  # 1-based, 没命中向量召回则 None
    bm25_rank: int | None  # 1-based, 没命中 BM25 则 None


@dataclass(frozen=True, slots=True)
class HybridSearchOutput:
    """检索 + stats. ``results`` 已按最终分数 (score) 降序."""

    results: list[SearchResult] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


# ─── SQL 模板 ───────────────────────────────────────────────────────────────

# 注: vector 距离用 ``<=>`` (cosine distance); 越小越近
#     similarity = 1 - distance, 留给上层展示用
_VECTOR_SQL = """
SELECT
    chunk_id,
    doc_id,
    ipo_code,
    chunk_index,
    page,
    text AS chunk_text,
    (embedding <=> CAST(:q_emb AS vector)) AS distance
FROM ipo_documents
WHERE embedding IS NOT NULL
  AND embedding_dim = :emb_dim
  {filter_sql}
ORDER BY embedding <=> CAST(:q_emb AS vector)
LIMIT :k
"""

_BM25_SQL = """
SELECT
    chunk_id,
    doc_id,
    ipo_code,
    chunk_index,
    page,
    text AS chunk_text,
    ts_rank_cd(tsv, plainto_tsquery('simple', :q_text)) AS bm25
FROM ipo_documents
WHERE tsv @@ plainto_tsquery('simple', :q_text)
  {filter_sql}
ORDER BY bm25 DESC
LIMIT :k
"""


def _build_filter_sql(
    *,
    ipo_code: str | None,
    doc_type: str | None,
    lang: str | None,
) -> tuple[str, dict[str, Any]]:
    """组装 ``ipo_code`` / ``doc_type`` / ``lang`` 过滤子句.

    返回 (SQL 片段含前导 ``AND``, 绑定参数字典). 任一字段为 None 时不参与过滤.
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if ipo_code is not None:
        clauses.append("AND ipo_code = :ipo_code")
        params["ipo_code"] = ipo_code
    if doc_type is not None:
        clauses.append("AND doc_type = :doc_type")
        params["doc_type"] = doc_type
    if lang is not None:
        clauses.append("AND lang = :lang")
        params["lang"] = lang
    return ("\n  ".join(clauses), params)


# ─── 主入口 ────────────────────────────────────────────────────────────────


async def hybrid_search(
    session: AsyncSession,
    query: str,
    *,
    ipo_code: str | None = None,
    doc_type: str | None = "prospectus",
    lang: str | None = None,
    final_top_k: int | None = None,
    vector_top_k: int | None = None,
    bm25_top_k: int | None = None,
    rrf_k: int | None = None,
    rerank_pool: int | None = None,
    use_rerank: bool | None = None,
    query_embedding: list[float] | None = None,
    settings: Settings | None = None,
) -> HybridSearchOutput:
    """对 ``query`` 跑混合检索, 返回 top-K ``SearchResult``.

    所有 ``None`` 参数走 ``settings`` 默认值 (在测试 / 调用方覆盖时直接传值).

    ``query_embedding`` 调用方可传入 (例如 BE-S2-007 LangGraph 主循环已 batch
    embed 过), 不传则内部调用 ``llm_client.embed`` 算一次.

    错误策略:
    - vector 召回失败 (embed 错 / SQL 错): logger.exception + 仅用 BM25
    - BM25 召回失败 (SQL 错): logger.exception + 仅用 vector
    - 两路都失败: 抛 (上层调用方需要明确知道检索完全坏掉)
    - rerank 失败: logger.warning + 走 RRF 顺序

    raises: ``ValueError`` (空 query) / ``RuntimeError`` (两路 SQL 都挂)
    """
    if not query or not query.strip():
        raise ValueError("hybrid_search: query 不能为空")

    s = settings or get_settings()
    final_top_k = final_top_k if final_top_k is not None else s.rag_final_top_k
    vector_top_k = vector_top_k if vector_top_k is not None else s.rag_vector_top_k
    bm25_top_k = bm25_top_k if bm25_top_k is not None else s.rag_bm25_top_k
    rrf_k = rrf_k if rrf_k is not None else s.rag_rrf_k
    rerank_pool = rerank_pool if rerank_pool is not None else s.rag_rerank_pool_size
    use_rerank = use_rerank if use_rerank is not None else s.rag_use_rerank

    if final_top_k <= 0 or vector_top_k <= 0 or bm25_top_k <= 0:
        raise ValueError(
            f"top_k 必须 > 0; got final={final_top_k} vec={vector_top_k} bm25={bm25_top_k}"
        )
    if rrf_k <= 0:
        raise ValueError(f"rrf_k 必须 > 0; got {rrf_k}")
    if rerank_pool < final_top_k:
        # rerank pool 不能比 final_top_k 还小, 否则 rerank 没意义
        rerank_pool = final_top_k

    t_start = time.monotonic()
    stats: dict[str, Any] = {
        "vector_hits": 0,
        "bm25_hits": 0,
        "fused_count": 0,
        "reranked": False,
        "elapsed_ms": 0,
    }

    filter_sql, filter_params = _build_filter_sql(
        ipo_code=ipo_code, doc_type=doc_type, lang=lang
    )

    # ── 路径 A: 向量召回 ──────────────────────────────────────────────────
    vector_rows: list[dict[str, Any]] = []
    vector_failed = False
    try:
        if query_embedding is None:
            emb_result = await embed([query])
            if not emb_result.embeddings:
                raise LLMError(
                    "embed query returned empty",
                    provider=emb_result.provider,
                    model=emb_result.model,
                )
            query_embedding = emb_result.embeddings[0]

        if len(query_embedding) != s.llm_embedding_dim:
            raise LLMError(
                f"query embedding dim {len(query_embedding)} != settings "
                f"{s.llm_embedding_dim}",
                provider="local",
                model=s.llm_embedding_model,
            )

        vector_rows = await _run_vector_sql(
            session,
            query_embedding,
            top_k=vector_top_k,
            filter_sql=filter_sql,
            filter_params=filter_params,
            emb_dim=s.llm_embedding_dim,
        )
        stats["vector_hits"] = len(vector_rows)
    except Exception as e:
        logger.exception(f"hybrid_search.vector_failed query_len={len(query)}: {e}")
        vector_failed = True

    # ── 路径 B: BM25 召回 ─────────────────────────────────────────────────
    bm25_rows: list[dict[str, Any]] = []
    bm25_failed = False
    bm25_query = _cjk_presplit(query).strip()
    if not bm25_query:
        # 全标点 / 全停用词的 query, 跳过 BM25
        logger.info("hybrid_search.bm25_skipped empty_after_presplit")
    else:
        try:
            bm25_rows = await _run_bm25_sql(
                session,
                bm25_query,
                top_k=bm25_top_k,
                filter_sql=filter_sql,
                filter_params=filter_params,
            )
            stats["bm25_hits"] = len(bm25_rows)
        except Exception as e:
            logger.exception(f"hybrid_search.bm25_failed query_len={len(query)}: {e}")
            bm25_failed = True

    if vector_failed and bm25_failed:
        raise RuntimeError("hybrid_search: both vector and BM25 paths failed")

    # ── 路径 C: RRF 融合 ──────────────────────────────────────────────────
    fused = _rrf_fuse(vector_rows, bm25_rows, rrf_k=rrf_k)
    stats["fused_count"] = len(fused)
    if not fused:
        stats["elapsed_ms"] = int((time.monotonic() - t_start) * 1000)
        return HybridSearchOutput(results=[], stats=stats)

    # ── 路径 D: rerank 二阶段 (可选) ──────────────────────────────────────
    rerank_candidates = fused[:rerank_pool]
    final_ordered: list[tuple[dict[str, Any], float]] = []  # (entry, final_score)

    if use_rerank and len(rerank_candidates) > 1:
        try:
            rerank_result = await rerank(
                query,
                [c["chunk_text"] for c in rerank_candidates],
                top_n=min(final_top_k, len(rerank_candidates)),
            )
            stats["reranked"] = True
            for orig_idx, score in rerank_result.results:
                if 0 <= orig_idx < len(rerank_candidates):
                    final_ordered.append((rerank_candidates[orig_idx], score))
        except (LLMError, Exception) as e:  # noqa: BLE001
            logger.warning(f"hybrid_search.rerank_failed fallback_to_rrf: {e}")
            final_ordered = [(c, c["rrf_score"]) for c in rerank_candidates]
    else:
        final_ordered = [(c, c["rrf_score"]) for c in rerank_candidates]

    # 截断到 final_top_k
    final_ordered = final_ordered[:final_top_k]

    out: list[SearchResult] = []
    for entry, score in final_ordered:
        out.append(
            SearchResult(
                chunk_id=entry["chunk_id"],
                doc_id=entry["doc_id"],
                ipo_code=entry["ipo_code"],
                chunk_index=entry["chunk_index"],
                page=entry["page"],
                text=entry["chunk_text"],
                score=float(score),
                rrf_score=float(entry["rrf_score"]),
                vector_rank=entry.get("vector_rank"),
                bm25_rank=entry.get("bm25_rank"),
            )
        )

    stats["elapsed_ms"] = int((time.monotonic() - t_start) * 1000)
    logger.info(
        f"hybrid_search.ok q_len={len(query)} vec={stats['vector_hits']} "
        f"bm25={stats['bm25_hits']} fused={stats['fused_count']} "
        f"rerank={stats['reranked']} returned={len(out)} "
        f"elapsed={stats['elapsed_ms']}ms"
    )
    return HybridSearchOutput(results=out, stats=stats)


# ─── 内部: SQL 执行 ────────────────────────────────────────────────────────


async def _run_vector_sql(
    session: AsyncSession,
    query_embedding: list[float],
    *,
    top_k: int,
    filter_sql: str,
    filter_params: dict[str, Any],
    emb_dim: int,
) -> list[dict[str, Any]]:
    """跑 HNSW cosine 召回. 返回原始行 (dict) 列表, 不做 RRF.

    pgvector 接受 ``CAST(:q_emb AS vector)`` 时 :q_emb 必须是 ``str``
    形如 ``"[1.0, 2.0, ...]"`` (asyncpg + pgvector 标准做法).
    """
    sql = _VECTOR_SQL.format(filter_sql=filter_sql)
    stmt = text(sql).bindparams(
        bindparam("q_emb", type_=String()),
        bindparam("k", type_=Integer()),
        bindparam("emb_dim", type_=Integer()),
    )
    params = {
        "q_emb": _vec_to_pg_literal(query_embedding),
        "k": top_k,
        "emb_dim": emb_dim,
        **filter_params,
    }
    result = await session.execute(stmt, params)
    rows = result.mappings().all()
    return [dict(r) for r in rows]


async def _run_bm25_sql(
    session: AsyncSession,
    bm25_query: str,
    *,
    top_k: int,
    filter_sql: str,
    filter_params: dict[str, Any],
) -> list[dict[str, Any]]:
    """跑 PG ``ts_rank_cd`` BM25 召回. 返回原始行 (dict) 列表."""
    sql = _BM25_SQL.format(filter_sql=filter_sql)
    stmt = text(sql).bindparams(
        bindparam("q_text", type_=String()),
        bindparam("k", type_=Integer()),
    )
    params = {
        "q_text": bm25_query,
        "k": top_k,
        **filter_params,
    }
    result = await session.execute(stmt, params)
    rows = result.mappings().all()
    return [dict(r) for r in rows]


def _vec_to_pg_literal(vec: list[float]) -> str:
    """把 1024-d 浮点 list 转 pgvector 字面量字符串 ``[v1,v2,...]``.

    asyncpg 驱动配 pgvector 不支持原生 list, 走文本字面量最简单稳定.
    """
    return "[" + ",".join(format(float(v), ".8f") for v in vec) + "]"


# ─── 内部: RRF 融合 ────────────────────────────────────────────────────────


def _rrf_fuse(
    vector_rows: list[dict[str, Any]],
    bm25_rows: list[dict[str, Any]],
    *,
    rrf_k: int,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion (Cormack 2009).

    score(d) = Σ 1/(rrf_k + rank_i(d))  for i in {vector, bm25}

    输入两路按各自分数已排好序, 1-based rank. 返回融合后按 ``rrf_score`` 降序的
    候选行 (dict, 注入 ``rrf_score`` / ``vector_rank`` / ``bm25_rank``).
    """
    pool: dict[uuid.UUID, dict[str, Any]] = {}

    for rank, row in enumerate(vector_rows, start=1):
        cid = row["chunk_id"]
        contrib = 1.0 / (rrf_k + rank)
        entry = pool.setdefault(cid, _new_entry(row))
        entry["rrf_score"] = entry.get("rrf_score", 0.0) + contrib
        entry["vector_rank"] = rank

    for rank, row in enumerate(bm25_rows, start=1):
        cid = row["chunk_id"]
        contrib = 1.0 / (rrf_k + rank)
        entry = pool.setdefault(cid, _new_entry(row))
        entry["rrf_score"] = entry.get("rrf_score", 0.0) + contrib
        entry["bm25_rank"] = rank

    fused = list(pool.values())
    # 主排序: rrf_score DESC; 次排序: chunk_id 字符串 ASC (稳定) 防同分抖动
    fused.sort(key=lambda x: (-x["rrf_score"], str(x["chunk_id"])))
    return fused


def _new_entry(row: dict[str, Any]) -> dict[str, Any]:
    """从 SQL 行构造 RRF 池条目. 留 ``rrf_score`` / ``vector_rank`` / ``bm25_rank``
    三个累加字段空着, 由 ``_rrf_fuse`` 主循环填.
    """
    return {
        "chunk_id": row["chunk_id"],
        "doc_id": row["doc_id"],
        "ipo_code": row["ipo_code"],
        "chunk_index": row["chunk_index"],
        "page": row["page"],
        "chunk_text": row["chunk_text"],
        "rrf_score": 0.0,
        "vector_rank": None,
        "bm25_rank": None,
    }


# 类型导出仅为 IDE / mypy 可见; 当前唯一对外入口 hybrid_search
__all__ = [
    "HybridSearchOutput",
    "SearchResult",
    "hybrid_search",
]


# 让 mypy 不抱怨未用 import (留作 BE-S2-007 拉 chunk 行的潜在类型 hint)
_ = (PG_UUID, Float)
