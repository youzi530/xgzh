"""BE-S2-003 集成测试: ipo_documents 0003 扩展 + RAG 路径关键不变量.

覆盖 (6 条用例):
1. 6 个新列存在 + 类型 + 默认值正确 (information_schema)
2. ``(doc_id, content_hash)`` partial UNIQUE 防重生效; ``content_hash IS NULL``
   不被卡, 老 Sprint 1 行可以共存
3. ``(doc_id, chunk_index)`` partial 索引让 ORDER BY chunk_index 顺序还原
4. ``vector(1024)`` 实际可写可查 + 按 cosine 距离 ORDER BY 拉相似 chunk
   (BE-S2-005 混合检索的核心原语)
5. ``embedding_model`` / ``embedding_dim`` / ``lang`` 三个 NOT NULL DEFAULT
   列, 不传也能写; HK 招股书可显式覆盖 lang='en'
6. 0003 downgrade 后 6 列 + 2 索引完全消失; upgrade head 后又回来
   (Sprint 1 的 ``ipo_documents`` 表 + HNSW + Sprint 1 老索引 全程不动)

不在本文件 (留 BE-S2-004 / BE-S2-005)
=====================================
- 真招股书 PDF 解析 / 切分 (BE-S2-004)
- 全文检索 + RRF 融合 (BE-S2-005, 还要等 0004 加 tsvector 列)
- HNSW ef_search runtime 调优 (BE-S2-005 应用层)
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.db.models.ipo import IPODocument

pytestmark = pytest.mark.db


# ─── 1. schema 形状 ───────────────────────────────────────────────────────


async def test_0003_added_six_columns_with_correct_defaults(
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """6 个新列必须按 0003 spec 落盘: nullable / type / default 全对得上."""
    expected: dict[str, dict[str, str | bool | None]] = {
        "chunk_index": {"data_type": "integer", "is_nullable": True, "default": None},
        "token_count": {"data_type": "integer", "is_nullable": True, "default": None},
        "content_hash": {
            "data_type": "character",  # CHAR(64); PG 报 'character'
            "is_nullable": True,
            "default": None,
        },
        "embedding_model": {
            "data_type": "character varying",
            "is_nullable": False,
            "default": "'BAAI/bge-m3'::character varying",
        },
        "embedding_dim": {
            "data_type": "integer",
            "is_nullable": False,
            "default": "1024",
        },
        "lang": {
            "data_type": "character varying",
            "is_nullable": False,
            "default": "'zh'::character varying",
        },
    }
    async with db_engine.connect() as conn:
        for col, want in expected.items():
            row = (
                await conn.execute(
                    text(
                        "SELECT data_type, is_nullable, column_default "
                        "FROM information_schema.columns "
                        "WHERE table_name='ipo_documents' AND column_name=:c"
                    ),
                    {"c": col},
                )
            ).one()
            assert row.data_type == want["data_type"], f"{col}.data_type={row.data_type}"
            is_null = row.is_nullable == "YES"
            assert is_null == want["is_nullable"], f"{col}.nullable={row.is_nullable}"
            if want["default"] is None:
                assert row.column_default is None, f"{col}.default={row.column_default}"
            else:
                assert want["default"] in (row.column_default or ""), (
                    f"{col}.default={row.column_default!r} expected ~{want['default']!r}"
                )


# ─── 2. partial UNIQUE 防重 ──────────────────────────────────────────────


async def _insert_chunk(
    session: AsyncSession,
    *,
    doc_id: str,
    content_hash: str | None,
    text_content: str = "lorem ipsum",
    chunk_index: int | None = None,
) -> uuid.UUID:
    chunk = IPODocument(
        doc_id=doc_id,
        doc_type="prospectus",
        text_content=text_content,
        content_hash=content_hash,
        chunk_index=chunk_index,
    )
    session.add(chunk)
    await session.flush()
    return chunk.chunk_id


async def test_partial_unique_blocks_duplicate_doc_id_content_hash(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """同 (doc_id, content_hash) 第二次插入抛 IntegrityError; 不同 hash 共存 OK."""
    h1 = hashlib.sha256(b"chunk-1 text").hexdigest()
    h2 = hashlib.sha256(b"chunk-2 text").hexdigest()

    async with session_factory() as s:
        await _insert_chunk(s, doc_id="doc-A", content_hash=h1)
        await _insert_chunk(s, doc_id="doc-A", content_hash=h2)  # 不同 hash 可写
        await _insert_chunk(s, doc_id="doc-B", content_hash=h1)  # 不同 doc 也可写
        await s.commit()

    # 第二次插入相同 (doc-A, h1) 应抛 IntegrityError. 注意: PG unique violation
    # 在 flush 阶段就抛 (asyncpg 立即报), 不等 commit; 因此 raises 必须包到
    # _insert_chunk 上, 而不是 s.commit() 上.
    async with session_factory() as s:
        with pytest.raises(IntegrityError):
            await _insert_chunk(s, doc_id="doc-A", content_hash=h1)


async def test_partial_unique_allows_multiple_null_content_hash(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """``content_hash IS NULL`` 行不受 UNIQUE 约束 — 兼容 Sprint 1 老数据 / 测试桩."""
    async with session_factory() as s:
        await _insert_chunk(s, doc_id="doc-Y", content_hash=None)
        await _insert_chunk(s, doc_id="doc-Y", content_hash=None)
        await _insert_chunk(s, doc_id="doc-Y", content_hash=None)
        await s.commit()

    async with session_factory() as s:
        cnt = (
            await s.execute(
                text(
                    "SELECT count(*) FROM ipo_documents "
                    "WHERE doc_id='doc-Y' AND content_hash IS NULL"
                )
            )
        ).scalar_one()
        assert cnt == 3


# ─── 3. chunk_index 顺序还原 ─────────────────────────────────────────────


async def test_chunk_index_orders_chunks_within_doc(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """切分后乱序入库, ORDER BY chunk_index ASC 还原原始招股书段落顺序."""
    async with session_factory() as s:
        # 故意打乱写入顺序 (模拟并发批量 ingest)
        for idx in [3, 0, 4, 1, 2]:
            await _insert_chunk(
                s,
                doc_id="prospectus-0700",
                content_hash=hashlib.sha256(f"para-{idx}".encode()).hexdigest(),
                chunk_index=idx,
                text_content=f"段落 {idx}",
            )
        await s.commit()

    async with session_factory() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT chunk_index, text FROM ipo_documents "
                    "WHERE doc_id=:d AND chunk_index IS NOT NULL "
                    "ORDER BY chunk_index ASC"
                ),
                {"d": "prospectus-0700"},
            )
        ).all()
    assert [r.chunk_index for r in rows] == [0, 1, 2, 3, 4]
    assert rows[0].text == "段落 0"
    assert rows[-1].text == "段落 4"


# ─── 4. vector(1024) 实写实查 + cosine 距离排序 ──────────────────────────


def _normalized_unit_vector(seed: int, dim: int = 1024) -> list[float]:
    """生成单位长度的伪向量, 让 cosine 距离测试有稳定可比性.

    种子相同 → 向量相同; 不依赖 numpy 节省测试依赖.
    公式带 ``+1`` 偏置, 防 seed=0 时全 0 → norm=0 → cosine 距离 NaN.
    """
    import math

    raw = [math.sin(seed * 0.13 + i * 0.01 + 1.0) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    assert norm > 0  # 安全网: seed/dim 组合永远算不出 0 范数
    return [x / norm for x in raw]


async def test_vector_1024_write_and_cosine_neighbor_search(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """实际灌 5 个 1024 维向量 + ANN 近邻查询, 验证 BE-S2-005 检索原语可用."""
    async with session_factory() as s:
        for seed in range(5):
            chunk = IPODocument(
                doc_id=f"vec-doc-{seed}",
                doc_type="prospectus",
                text_content=f"chunk {seed}",
                embedding=_normalized_unit_vector(seed),
                content_hash=hashlib.sha256(f"v{seed}".encode()).hexdigest(),
                chunk_index=seed,
            )
            s.add(chunk)
        await s.commit()

    # 用 seed=0 当 query, 期望 doc_id='vec-doc-0' 距离最小 (=0)
    query_vec = _normalized_unit_vector(0)
    query_str = "[" + ",".join(f"{v:.6f}" for v in query_vec) + "]"

    async with session_factory() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT doc_id, embedding <=> CAST(:q AS vector) AS dist "
                    "FROM ipo_documents WHERE embedding IS NOT NULL "
                    "ORDER BY embedding <=> CAST(:q AS vector) ASC LIMIT 3"
                ),
                {"q": query_str},
            )
        ).all()
    assert len(rows) == 3
    assert rows[0].doc_id == "vec-doc-0", f"top-1 应该是 self, 实际 {rows[0].doc_id}"
    assert rows[0].dist < 1e-5, f"self cosine distance ≈ 0, 实际 {rows[0].dist}"
    # 其余 chunk 距离严格 > 0 (向量不同)
    assert rows[1].dist > rows[0].dist
    assert rows[2].dist > rows[1].dist


# ─── 5. NOT NULL DEFAULT 列 ──────────────────────────────────────────────


async def test_default_columns_filled_when_omitted(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """``embedding_model`` / ``embedding_dim`` / ``lang`` 三个 NOT NULL 列不传也能写,
    PG server-side default 必须生效, 否则 BE-S2-004 老入库代码全得改.
    """
    async with session_factory() as s:
        chunk_id = await _insert_chunk(
            s, doc_id="doc-default", content_hash=hashlib.sha256(b"x").hexdigest()
        )
        await s.commit()

    async with session_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT embedding_model, embedding_dim, lang "
                    "FROM ipo_documents WHERE chunk_id=:c"
                ),
                {"c": chunk_id},
            )
        ).one()
    assert row.embedding_model == "BAAI/bge-m3"
    assert row.embedding_dim == 1024
    assert row.lang == "zh"


async def test_lang_can_be_overridden_for_hk_english_prospectus(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """HK 招股书是英文; lang='en' 必须能显式覆盖默认 'zh'."""
    async with session_factory() as s:
        chunk = IPODocument(
            doc_id="hk-0700-prospectus",
            doc_type="prospectus",
            text_content="The company has been incorporated under...",
            content_hash=hashlib.sha256(b"en-1").hexdigest(),
            lang="en",
        )
        s.add(chunk)
        await s.commit()
        chunk_id = chunk.chunk_id

    async with session_factory() as s:
        row = (
            await s.execute(
                text("SELECT lang FROM ipo_documents WHERE chunk_id=:c"),
                {"c": chunk_id},
            )
        ).one()
    assert row.lang == "en"


# ─── 6. downgrade 完整反向 + upgrade 重新就位 ───────────────────────────


def _build_alembic_config(test_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)
    return cfg


async def test_0003_downgrade_then_upgrade_is_idempotent(
    test_database_url: str,
    schema_at_head: str,  # noqa: ARG001
) -> None:
    """downgrade -1 后 6 列 + 2 索引消失, 表本身保留 (Sprint 1 列动也不动);
    upgrade head 后又全部回来. 这是 0003 这条 PR 必须满足的"可回滚"协议.
    """
    cfg = _build_alembic_config(test_database_url)

    new_cols = {
        "chunk_index",
        "token_count",
        "content_hash",
        "embedding_model",
        "embedding_dim",
        "lang",
    }
    new_indexes = {
        "uq_ipo_documents_doc_id_content_hash",
        "ix_ipo_documents_doc_id_chunk_index",
    }
    sprint1_cols = {
        "chunk_id",
        "ipo_id",
        "ipo_code",
        "doc_id",
        "doc_type",
        "section",
        "page",
        "text",
        "embedding",
        "metadata",
        "created_at",
        "updated_at",
    }

    engine = create_async_engine(test_database_url)
    try:
        # 先确认起点: 当前 head (含 0003) 6 列 + 2 索引齐全
        async with engine.connect() as conn:
            cols = {
                r[0]
                for r in (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name='ipo_documents'"
                        )
                    )
                ).all()
            }
            assert new_cols.issubset(cols), f"head 状态缺新列: {new_cols - cols}"

        # downgrade -1 → 回到 0002_chat
        await asyncio.to_thread(command.downgrade, cfg, "-1")

        async with engine.connect() as conn:
            cols = {
                r[0]
                for r in (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name='ipo_documents'"
                        )
                    )
                ).all()
            }
            idx = {
                r[0]
                for r in (
                    await conn.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname='public' AND tablename='ipo_documents'"
                        )
                    )
                ).all()
            }
        assert not (new_cols & cols), f"downgrade 后仍有新列: {new_cols & cols}"
        assert not (new_indexes & idx), f"downgrade 后仍有新索引: {new_indexes & idx}"
        assert sprint1_cols.issubset(cols), (
            f"downgrade 把 Sprint 1 老列也擦了: {sprint1_cols - cols}"
        )
        assert "ix_ipo_documents_embedding_hnsw" in idx, "Sprint 1 HNSW 索引被误删"

        # 再 upgrade head → 回到 0003_chunks 全功能
        await asyncio.to_thread(command.upgrade, cfg, "head")

        async with engine.connect() as conn:
            cols = {
                r[0]
                for r in (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name='ipo_documents'"
                        )
                    )
                ).all()
            }
            idx = {
                r[0]
                for r in (
                    await conn.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname='public' AND tablename='ipo_documents'"
                        )
                    )
                ).all()
            }
        assert new_cols.issubset(cols), f"upgrade 后仍缺新列: {new_cols - cols}"
        assert new_indexes.issubset(idx), f"upgrade 后仍缺新索引: {new_indexes - idx}"
    finally:
        await engine.dispose()
