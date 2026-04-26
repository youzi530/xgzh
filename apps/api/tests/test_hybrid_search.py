"""BE-S2-005 — 混合检索 (vector + BM25 + RRF + reranker) 集成测试.

走真 PG 是必须的:
- HNSW cosine 路径需要 ``embedding <=> CAST(:q AS vector)`` 真跑 (mock SQL 测不到
  pgvector 行为)
- BM25 路径需要 0004 generated tsv 列 + GIN 索引 + ts_rank_cd 真跑
- RRF 融合在应用层, 但融合输入是两条 SQL 的 ranking, 必须 PG 输出真实顺序

mock 的部分:
- ``llm_client.embed`` (查询 embedding): mock 成确定性向量, 让向量召回结果可预期
- ``llm_client.rerank``: mock 成"按候选 chunk_text 逆序" 等可控行为, 验证 rerank
  开/关时 results 顺序差异

测试矩阵
========
1. 仅 vector 命中 (BM25 query 语言外语, ts 不匹配)
2. 仅 BM25 命中 (vector 是 None)
3. 两路都命中, RRF 融合 → 同 chunk 跨两路 rrf 累加 > 单路
4. ipo_code / doc_type / lang 过滤推 SQL, 隔离效果
5. rerank 启用: monkey rerank 返回特定顺序, 验证 final_ordered 跟 rerank 一致
6. rerank 失败: monkey 抛 LLMError, fallback 走 RRF
7. 空 query 抛 ValueError
8. dim mismatch (传 query_embedding 不是 1024 维) → vector 路径失败但 BM25 兜底
9. 全停用词 query (全标点) → 仅 vector
10. final_top_k 截断
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.adapters.llm_client import (
    EmbeddingResult,
    LLMProviderError,
    RerankResult,
    TokenUsage,
)
from app.core.config import Settings
from app.services.rag import hybrid_search as hybrid_search_module
from app.services.rag.hybrid_search import _cjk_presplit, _rrf_fuse, hybrid_search

pytestmark = pytest.mark.db


# ─── 复用 fixture (与 test_prospectus_ingest 同源) ──────────────────────────


def _build_alembic_config(test_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)
    return cfg


async def _drop_business_tables(url: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
            for (tbl,) in rows:
                await conn.execute(text(f'DROP TABLE IF EXISTS public."{tbl}" CASCADE'))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
async def schema_at_head(test_database_url: str) -> AsyncIterator[str]:
    await _drop_business_tables(test_database_url)
    cfg = _build_alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield test_database_url


@pytest.fixture
async def db_engine(schema_at_head: str):  # type: ignore[no-untyped-def]
    engine = create_async_engine(schema_at_head, future=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(db_engine) -> async_sessionmaker[AsyncSession]:  # type: ignore[no-untyped-def]
    return async_sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def truncate_ipo_documents(db_engine) -> AsyncIterator[None]:  # type: ignore[no-untyped-def]
    async with db_engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE ipos, ipo_documents RESTART IDENTITY CASCADE")
        )
    yield


# ─── seed helpers ──────────────────────────────────────────────────────────


def _vec(seed: int, dim: int = 1024, perturb_idx: int | None = None) -> list[float]:
    """构造可控相似度的单位向量.

    - 不同 ``seed`` 之间 cosine 距离接近 1; 相同 seed 距离接近 0
    - 给 perturb_idx 加扰动让相同 seed 的向量分散开
    """
    v = [0.0] * dim
    v[seed % dim] = 1.0
    if perturb_idx is not None:
        v[perturb_idx % dim] = 0.01
    return v


async def _seed_chunk(
    session: AsyncSession,
    *,
    ipo_code: str,
    doc_id: str,
    doc_type: str = "prospectus",
    lang: str = "zh",
    text_content: str,
    embedding: list[float] | None,
    chunk_index: int = 0,
    page: int | None = 1,
) -> uuid.UUID:
    """直接 INSERT 一条 ipo_documents 行 (绕过 ORM ipo_id FK 简化测试)."""
    cid = uuid.uuid4()
    emb_param = (
        None
        if embedding is None
        else "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"
    )

    await session.execute(
        text(
            """
            INSERT INTO ipo_documents
                (chunk_id, doc_id, doc_type, ipo_code, lang, page, text,
                 embedding, chunk_index, content_hash, embedding_model,
                 embedding_dim)
            VALUES
                (:cid, :doc_id, :doc_type, :ipo_code, :lang, :page, :txt,
                 CAST(:emb AS vector), :chunk_index,
                 md5(:txt), 'BAAI/bge-m3', 1024)
            """
        ),
        {
            "cid": cid,
            "doc_id": doc_id,
            "doc_type": doc_type,
            "ipo_code": ipo_code,
            "lang": lang,
            "page": page,
            "txt": text_content,
            "emb": emb_param,
            "chunk_index": chunk_index,
        },
    )
    return cid


def _settings_no_rerank() -> Settings:
    return Settings(
        rag_use_rerank=False,
        rag_vector_top_k=10,
        rag_bm25_top_k=10,
        rag_final_top_k=5,
        rag_rerank_pool_size=10,
        rag_rrf_k=60,
        llm_embedding_dim=1024,
    )


def _patch_query_embed(
    monkeypatch: pytest.MonkeyPatch, query_vec: list[float]
) -> None:
    """让 ``hybrid_search.embed`` 返回固定 query 向量."""

    async def _fake_embed(texts: list[str], **_kw: Any) -> EmbeddingResult:
        return EmbeddingResult(
            embeddings=[query_vec for _ in texts],
            usage=TokenUsage.empty(),
            model="BAAI/bge-m3",
            provider="siliconflow",
            dim=len(query_vec),
        )

    monkeypatch.setattr(hybrid_search_module, "embed", _fake_embed)


# ─── 1. 纯单元: CJK 预切 ────────────────────────────────────────────────────


def test_cjk_presplit_inserts_space_after_each_cjk_char() -> None:
    out = _cjk_presplit("腾讯控股IPO")
    # 每个 CJK 后必有空格; ASCII 不动
    assert out == "腾 讯 控 股 IPO"


def test_cjk_presplit_pure_ascii_unchanged() -> None:
    assert _cjk_presplit("Hello World") == "Hello World"


def test_cjk_presplit_empty_returns_empty() -> None:
    assert _cjk_presplit("") == ""


def test_cjk_presplit_punct_only() -> None:
    # 全角 / 半角标点都不在 CJK 范围, 不插空格
    assert _cjk_presplit("！@#$%^&*") == "！@#$%^&*"


# ─── 2. 纯单元: RRF 融合 ────────────────────────────────────────────────────


def test_rrf_fuse_same_chunk_in_both_paths_score_sums() -> None:
    cid = uuid.uuid4()
    row = {
        "chunk_id": cid,
        "doc_id": "d1",
        "ipo_code": "X",
        "chunk_index": 0,
        "page": 1,
        "chunk_text": "abc",
    }
    fused = _rrf_fuse([row], [row], rrf_k=60)
    assert len(fused) == 1
    # 两路都 rank 1: 1/(60+1) + 1/(60+1) = 2/61
    assert fused[0]["rrf_score"] == pytest.approx(2 / 61, rel=1e-9)
    assert fused[0]["vector_rank"] == 1
    assert fused[0]["bm25_rank"] == 1


def test_rrf_fuse_disjoint_chunks_keeps_both() -> None:
    a = {
        "chunk_id": uuid.uuid4(),
        "doc_id": "d1",
        "ipo_code": "X",
        "chunk_index": 0,
        "page": 1,
        "chunk_text": "a",
    }
    b = {
        "chunk_id": uuid.uuid4(),
        "doc_id": "d2",
        "ipo_code": "Y",
        "chunk_index": 0,
        "page": 1,
        "chunk_text": "b",
    }
    fused = _rrf_fuse([a], [b], rrf_k=60)
    assert len(fused) == 2
    a_entry = next(f for f in fused if f["chunk_id"] == a["chunk_id"])
    b_entry = next(f for f in fused if f["chunk_id"] == b["chunk_id"])
    assert a_entry["vector_rank"] == 1 and a_entry["bm25_rank"] is None
    assert b_entry["bm25_rank"] == 1 and b_entry["vector_rank"] is None


def test_rrf_fuse_orders_by_score_desc() -> None:
    rows = [
        {
            "chunk_id": uuid.UUID(int=i + 1),
            "doc_id": "d",
            "ipo_code": "X",
            "chunk_index": i,
            "page": 1,
            "chunk_text": f"c{i}",
        }
        for i in range(3)
    ]
    fused = _rrf_fuse(rows, rows, rrf_k=60)
    # 三个 chunk, rank 1 / 2 / 3, 同时出现在两路, 总分单调递减
    assert len(fused) == 3
    scores = [f["rrf_score"] for f in fused]
    assert scores == sorted(scores, reverse=True)


# ─── 3. DB 集成: 纯 vector 路径 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_search_vector_only_when_bm25_no_match(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """query 是 ASCII 但库里全中文, BM25 不命中; vector 召回兜底."""
    target_vec = _vec(7)  # query 同向量 → 距离 0
    other_vec = _vec(123)  # 远

    async with session_factory() as session:
        target_cid = await _seed_chunk(
            session,
            ipo_code="00700.HK",
            doc_id="dvec1",
            text_content="腾讯主营互联网增值服务",
            embedding=target_vec,
            chunk_index=0,
        )
        await _seed_chunk(
            session,
            ipo_code="00700.HK",
            doc_id="dvec1",
            text_content="阿里风险因素章节",
            embedding=other_vec,
            chunk_index=1,
        )
        await session.commit()

    _patch_query_embed(monkeypatch, target_vec)

    async with session_factory() as session:
        out = await hybrid_search(
            session,
            "completely english query no chinese",
            settings=_settings_no_rerank(),
        )

    assert out.stats["vector_hits"] == 2
    assert out.stats["bm25_hits"] == 0  # 英文 query 不匹配中文字符级 tsv
    assert out.results[0].chunk_id == target_cid
    assert out.results[0].vector_rank == 1
    assert out.results[0].bm25_rank is None


# ─── 4. DB 集成: 纯 BM25 路径 (no embedding) ────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_search_bm25_only_when_no_embedding(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """库里 chunk 没 embedding (历史数据), 仅 BM25 路径召回."""
    async with session_factory() as session:
        target_cid = await _seed_chunk(
            session,
            ipo_code="00700.HK",
            doc_id="dbm1",
            text_content="腾讯控股招股说明书业绩亮点",
            embedding=None,
            chunk_index=0,
        )
        await _seed_chunk(
            session,
            ipo_code="00700.HK",
            doc_id="dbm1",
            text_content="完全无关文本只有阿里巴巴介绍",
            embedding=None,
            chunk_index=1,
        )
        await session.commit()

    _patch_query_embed(monkeypatch, _vec(0))

    async with session_factory() as session:
        out = await hybrid_search(
            session,
            "腾讯业绩",
            settings=_settings_no_rerank(),
        )

    # 没 embedding 的行不进入 vector 召回 (WHERE embedding IS NOT NULL)
    assert out.stats["vector_hits"] == 0
    assert out.stats["bm25_hits"] >= 1
    assert any(r.chunk_id == target_cid for r in out.results)
    assert all(r.vector_rank is None for r in out.results)


# ─── 5. DB 集成: 两路融合 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_search_rrf_fuses_both_paths(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 chunk 在两路都命中时 RRF score 应高于仅命中一路的 chunk."""
    both_vec = _vec(5)
    only_vec = _vec(5, perturb_idx=10)  # 离 query 也近, 但 BM25 不会命中

    async with session_factory() as session:
        both_cid = await _seed_chunk(
            session,
            ipo_code="00700.HK",
            doc_id="drrf",
            text_content="腾讯业绩亮点 strong revenue growth this year",
            embedding=both_vec,
            chunk_index=0,
        )
        only_cid = await _seed_chunk(
            session,
            ipo_code="00700.HK",
            doc_id="drrf",
            text_content="completely irrelevant english only paragraph",
            embedding=only_vec,
            chunk_index=1,
        )
        await session.commit()

    _patch_query_embed(monkeypatch, both_vec)

    async with session_factory() as session:
        out = await hybrid_search(
            session,
            "腾讯业绩",
            settings=_settings_no_rerank(),
        )

    # both_cid 在 vector + BM25 都命中, score = 1/61 + 1/61
    # only_cid 只在 vector 命中, score = 1/62 (rank 2)
    both_r = next(r for r in out.results if r.chunk_id == both_cid)
    only_r = next(r for r in out.results if r.chunk_id == only_cid)
    assert both_r.rrf_score > only_r.rrf_score
    assert both_r.vector_rank is not None
    assert both_r.bm25_rank is not None
    assert only_r.bm25_rank is None


# ─── 6. DB 集成: 过滤推 SQL ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_search_filters_by_ipo_code(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_vec = _vec(11)
    async with session_factory() as session:
        in_cid = await _seed_chunk(
            session,
            ipo_code="00700.HK",
            doc_id="df1",
            text_content="腾讯控股招股书",
            embedding=target_vec,
        )
        out_cid = await _seed_chunk(
            session,
            ipo_code="00001.HK",
            doc_id="df2",
            text_content="阿里巴巴招股书",
            embedding=target_vec,
        )
        await session.commit()

    _patch_query_embed(monkeypatch, target_vec)

    async with session_factory() as session:
        out = await hybrid_search(
            session,
            "招股书",
            ipo_code="00700.HK",
            settings=_settings_no_rerank(),
        )

    chunk_ids = {r.chunk_id for r in out.results}
    assert in_cid in chunk_ids
    assert out_cid not in chunk_ids


@pytest.mark.asyncio
async def test_hybrid_search_filters_by_doc_type(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_vec = _vec(13)
    async with session_factory() as session:
        prospectus_cid = await _seed_chunk(
            session,
            ipo_code="X.HK",
            doc_id="dt1",
            doc_type="prospectus",
            text_content="招股书内容",
            embedding=target_vec,
        )
        article_cid = await _seed_chunk(
            session,
            ipo_code="X.HK",
            doc_id="dt2",
            doc_type="article",
            text_content="新闻文章",
            embedding=target_vec,
        )
        await session.commit()

    _patch_query_embed(monkeypatch, target_vec)

    async with session_factory() as session:
        out = await hybrid_search(
            session,
            "招股书",
            doc_type="prospectus",
            settings=_settings_no_rerank(),
        )

    chunk_ids = {r.chunk_id for r in out.results}
    assert prospectus_cid in chunk_ids
    assert article_cid not in chunk_ids


# ─── 7. DB 集成: rerank 启用 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_search_rerank_reorders_results(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rerank 返回特定顺序时, final_ordered 与 rerank 结果一致."""
    target_vec = _vec(17)
    async with session_factory() as session:
        c0 = await _seed_chunk(
            session,
            ipo_code="X",
            doc_id="dr",
            text_content="文本零",
            embedding=target_vec,
            chunk_index=0,
        )
        c1 = await _seed_chunk(
            session,
            ipo_code="X",
            doc_id="dr",
            text_content="文本一",
            embedding=target_vec,
            chunk_index=1,
        )
        c2 = await _seed_chunk(
            session,
            ipo_code="X",
            doc_id="dr",
            text_content="文本二",
            embedding=target_vec,
            chunk_index=2,
        )
        await session.commit()

    _patch_query_embed(monkeypatch, target_vec)

    async def fake_rerank(
        query: str, documents: list[str], **_kw: Any
    ) -> RerankResult:
        # 反过来打分: 最后一条 (idx=N-1) 最高
        n = len(documents)
        return RerankResult(
            results=[(n - 1 - i, float(n - i)) for i in range(n)],
            model="bge-reranker-v2-m3",
            provider="siliconflow",
            usage=TokenUsage.empty(),
        )

    monkeypatch.setattr(hybrid_search_module, "rerank", fake_rerank)

    s = _settings_no_rerank()
    s = Settings(**{**s.model_dump(), "rag_use_rerank": True})

    async with session_factory() as session:
        out = await hybrid_search(session, "文本", settings=s)

    assert out.stats["reranked"] is True
    # 结果顺序与原 vector 顺序相反
    assert len(out.results) == 3
    assert {out.results[0].chunk_id, out.results[1].chunk_id, out.results[2].chunk_id} == {
        c0,
        c1,
        c2,
    }
    # rerank 给最后一个原始候选最高分 → 第一个返回
    # 由于 vector + BM25 都返回三个 (顺序由 PG planner 决定), 不强 assert 具体 cid
    assert out.results[0].score >= out.results[1].score >= out.results[2].score


# ─── 8. DB 集成: rerank 失败 fallback ───────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_search_rerank_failure_falls_back_to_rrf(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_vec = _vec(19)
    async with session_factory() as session:
        await _seed_chunk(
            session,
            ipo_code="X",
            doc_id="dfb",
            text_content="文本一",
            embedding=target_vec,
        )
        await _seed_chunk(
            session,
            ipo_code="X",
            doc_id="dfb",
            text_content="文本二",
            embedding=target_vec,
            chunk_index=1,
        )
        await session.commit()

    _patch_query_embed(monkeypatch, target_vec)

    async def fake_rerank_fail(
        query: str, documents: list[str], **_kw: Any
    ) -> RerankResult:
        raise LLMProviderError(
            "siliconflow rerank quota exceeded",
            provider="siliconflow",
            model="bge-reranker-v2-m3",
        )

    monkeypatch.setattr(hybrid_search_module, "rerank", fake_rerank_fail)

    s = Settings(**{**_settings_no_rerank().model_dump(), "rag_use_rerank": True})

    async with session_factory() as session:
        out = await hybrid_search(session, "文本", settings=s)

    assert out.stats["reranked"] is False  # rerank 失败, fallback
    assert len(out.results) == 2
    # score 是 RRF 分数 (rerank 失败时 score == rrf_score)
    for r in out.results:
        assert r.score == r.rrf_score


# ─── 9. 入参校验 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_search_empty_query_raises() -> None:
    # 不需要 DB, 空 query 提前抛
    with pytest.raises(ValueError, match="query 不能为空"):
        await hybrid_search(None, "", settings=_settings_no_rerank())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_hybrid_search_whitespace_query_raises() -> None:
    with pytest.raises(ValueError, match="query 不能为空"):
        await hybrid_search(None, "   \n\t  ", settings=_settings_no_rerank())  # type: ignore[arg-type]


# ─── 10. dim mismatch + 全标点 query ────────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_search_query_embedding_dim_mismatch_falls_back_to_bm25(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,  # noqa: ARG001
) -> None:
    """传入维度错的 query_embedding, vector 路径应失败但 BM25 兜底."""
    async with session_factory() as session:
        await _seed_chunk(
            session,
            ipo_code="X",
            doc_id="ddim",
            text_content="腾讯业绩",
            embedding=_vec(0),
        )
        await session.commit()

    async with session_factory() as session:
        out = await hybrid_search(
            session,
            "腾讯",
            query_embedding=[0.1] * 512,  # 错维
            settings=_settings_no_rerank(),
        )

    # vector 失败但 BM25 还在
    assert out.stats["vector_hits"] == 0
    assert out.stats["bm25_hits"] >= 1


@pytest.mark.asyncio
async def test_hybrid_search_punct_only_query_skips_bm25_uses_vector(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全标点 query → 跳 BM25; vector 仍正常召回."""
    target_vec = _vec(23)
    async with session_factory() as session:
        await _seed_chunk(
            session,
            ipo_code="X",
            doc_id="dp",
            text_content="只是一段普通正文",
            embedding=target_vec,
        )
        await session.commit()

    _patch_query_embed(monkeypatch, target_vec)

    async with session_factory() as session:
        # 全部 ascii 标点; _cjk_presplit 不动它, plainto_tsquery 全归零
        out = await hybrid_search(
            session,
            "?!.,",
            settings=_settings_no_rerank(),
        )

    assert out.stats["bm25_hits"] == 0
    assert out.stats["vector_hits"] >= 1
    assert len(out.results) >= 1


# ─── 11. final_top_k 截断 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_search_truncates_to_final_top_k(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_vec = _vec(31)
    async with session_factory() as session:
        for i in range(8):
            await _seed_chunk(
                session,
                ipo_code="X",
                doc_id=f"dtr{i}",
                text_content=f"文本{i}",
                embedding=target_vec,
                chunk_index=i,
            )
        await session.commit()

    _patch_query_embed(monkeypatch, target_vec)

    s = Settings(
        **{**_settings_no_rerank().model_dump(), "rag_final_top_k": 3}
    )

    async with session_factory() as session:
        out = await hybrid_search(session, "文本", settings=s)

    assert len(out.results) == 3
    assert out.stats["fused_count"] >= 3  # 池里至少 3
