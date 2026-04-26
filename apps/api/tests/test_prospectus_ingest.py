"""BE-S2-004 — 招股书入库流水线集成测试.

覆盖 ``app/services/rag/prospectus_ingest_service.py`` 的全链路:
  - 下载 (mock pdf_loader.fetch_pdf_bytes)
  - 解析 (走真 pypdf, 自构 minimal PDF bytes)
  - 切分 (走真 chunker.split_text)
  - embed (mock llm_client.embed → 固定 1024 维向量)
  - 入库 (真 PG, ON CONFLICT (doc_id, content_hash) DO NOTHING)

为什么走真 DB 而非纯单元
==========================
入库幂等键的核心保证就是 BE-S2-003 的 partial UNIQUE 索引;
mock DB 永远测不到, 必须真 PG ``\\d ipo_documents`` 跑出来.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.adapters import pdf_loader
from app.adapters.llm_client import (
    EmbeddingResult,
    LLMProviderError,
    TokenUsage,
)
from app.core.config import Settings
from app.db.models import IPO, IPODocument
from app.services.rag import prospectus_ingest_service

pytestmark = pytest.mark.db


# ─── DB fixtures (与 test_ipo_ingest 同源, 内联以保 PR diff 局部) ──────────


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


# ─── 测试用 minimal PDF + embed mock helpers ────────────────────────────────


def _build_pdf(*pages: str) -> bytes:
    """复用 test_pdf_loader 的同套 minimal PDF 构造法."""
    objs: list[bytes] = []
    page_obj_ids = list(range(3, 3 + len(pages)))
    content_obj_ids = list(range(3 + len(pages), 3 + 2 * len(pages)))
    font_id = 3 + 2 * len(pages)

    objs.append(b"1 0 obj\n<</Type /Catalog /Pages 2 0 R>>\nendobj")
    kids = " ".join(f"{i} 0 R" for i in page_obj_ids)
    objs.append(
        f"2 0 obj\n<</Type /Pages /Kids [{kids}] /Count {len(pages)}>>\nendobj".encode()
    )
    for page_id, content_id in zip(page_obj_ids, content_obj_ids, strict=True):
        objs.append(
            (
                f"{page_id} 0 obj\n<</Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 612 792] /Contents {content_id} 0 R "
                f"/Resources <</Font <</F1 {font_id} 0 R>>>>>>\nendobj"
            ).encode()
        )
    for content_id, page_text in zip(content_obj_ids, pages, strict=True):
        safe = page_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 50 750 Td ({safe}) Tj ET".encode()
        objs.append(
            (f"{content_id} 0 obj\n<</Length {len(stream)}>>\nstream\n".encode())
            + stream
            + b"\nendstream\nendobj"
        )
    objs.append(
        f"{font_id} 0 obj\n<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>\nendobj".encode()
    )

    header = b"%PDF-1.4\n"
    body = b""
    offsets: list[int] = []
    pos = len(header)
    for o in objs:
        offsets.append(pos)
        body += o + b"\n"
        pos += len(o) + 1

    n = len(objs)
    xref_pos = len(header) + len(body)
    xref = f"xref\n0 {n + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()
    trailer = (
        f"trailer\n<</Size {n + 1} /Root 1 0 R>>\nstartxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return header + body + xref + trailer


def _make_settings() -> Settings:
    """让 chunker 切出多个 chunk 的小阈值."""
    return Settings(
        rag_chunk_size_tokens=20,
        rag_chunk_overlap_tokens=2,
        llm_embedding_dim=1024,
    )


def _fake_embed_factory(dim: int = 1024):  # type: ignore[no-untyped-def]
    """返回一个 fake embed: 每个 chunk → 固定 dim 维 0.1 浮点向量."""
    call_count = {"n": 0}

    async def _fake_embed(
        texts: list[str], **_kwargs: Any
    ) -> EmbeddingResult:
        call_count["n"] += 1
        embeddings = [[0.1] * dim for _ in texts]
        return EmbeddingResult(
            embeddings=embeddings,
            usage=TokenUsage.empty(),
            model="BAAI/bge-m3",
            provider="siliconflow",
            dim=dim,
        )

    return _fake_embed, call_count


# ─── 测试 ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_ingest_prospectus_happy_full_pipeline(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正常路径: 下载 + 解析 + 切分 + embed + 入库一气呵成."""
    pdf = _build_pdf(
        "Section 1 about IPO basics " * 10,
        "Section 2 about financials " * 10,
        "Section 3 about risks " * 10,
    )

    async def fake_fetch(url: str, **_kwargs: Any) -> bytes:
        return pdf

    monkeypatch.setattr(pdf_loader, "fetch_pdf_bytes", fake_fetch)
    fake_embed, call_count = _fake_embed_factory()
    monkeypatch.setattr(prospectus_ingest_service, "embed", fake_embed)

    url = "https://www1.hkexnews.hk/path/0700.pdf"
    async with session_factory() as session:
        # 先种一行 ipos, 让 ipo_id resolve 走真路径
        from app.schemas.ipo import IPOItem
        from app.services.ipo_ingest_service import upsert_ipos

        await upsert_ipos(
            session,
            [
                IPOItem(
                    code="00700.HK",
                    name="腾讯控股",
                    market="HK",
                    status="upcoming",
                )
            ],
        )
        await session.commit()

    async with session_factory() as session:
        stats = await prospectus_ingest_service.run_ingest_prospectus(
            session,
            ipo_code="00700.HK",
            prospectus_url=url,
            settings=_make_settings(),
        )
        await session.commit()

    assert stats["stage"] == "ok"
    assert stats["errors"] == 0
    assert stats["pdf_pages"] == 3
    assert stats["extracted_pages"] == 3
    assert stats["chunks_total"] >= 3
    assert stats["inserted"] == stats["chunks_total"]
    assert stats["skipped_duplicates"] == 0
    assert call_count["n"] == 1  # 单次批量 embed (texts 不会超 32)

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(IPODocument).where(IPODocument.ipo_code == "00700.HK")
            )
        ).scalars().all()
        assert len(rows) == stats["inserted"]
        # chunk_index 0..N-1 单调
        indices = sorted(r.chunk_index for r in rows if r.chunk_index is not None)
        assert indices == list(range(len(rows)))
        # 全部归到同 doc_id
        doc_ids = {r.doc_id for r in rows}
        assert len(doc_ids) == 1
        assert len(next(iter(doc_ids))) == 32  # sha256 前 32 hex
        # 元数据齐
        for r in rows:
            assert r.doc_type == "prospectus"
            assert r.embedding_model == "BAAI/bge-m3"
            assert r.embedding_dim == 1024
            assert r.content_hash and len(r.content_hash) == 64
            assert r.token_count and r.token_count > 0
            assert r.embedding is not None
            assert len(r.embedding) == 1024
        # ipo_id 已 resolve
        ipo_ids = {r.ipo_id for r in rows}
        assert None not in ipo_ids


@pytest.mark.asyncio
async def test_run_ingest_prospectus_dedup_on_rerun(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重跑同 URL 时, 全部 chunk 走 ON CONFLICT DO NOTHING, 不再 INSERT."""
    pdf = _build_pdf("Identical content " * 30)

    async def fake_fetch(url: str, **_kwargs: Any) -> bytes:
        return pdf

    monkeypatch.setattr(pdf_loader, "fetch_pdf_bytes", fake_fetch)
    fake_embed, _ = _fake_embed_factory()
    monkeypatch.setattr(prospectus_ingest_service, "embed", fake_embed)

    url = "https://www1.hkexnews.hk/path/dup.pdf"
    settings = _make_settings()

    async with session_factory() as session:
        s1 = await prospectus_ingest_service.run_ingest_prospectus(
            session,
            ipo_code="00001.HK",
            prospectus_url=url,
            settings=settings,
        )
        await session.commit()

    async with session_factory() as session:
        s2 = await prospectus_ingest_service.run_ingest_prospectus(
            session,
            ipo_code="00001.HK",
            prospectus_url=url,
            settings=settings,
        )
        await session.commit()

    assert s1["inserted"] >= 1
    # 第二次: chunks_total / chunks_embedded 仍正常算, 但 inserted 应为 0
    assert s2["chunks_total"] == s1["chunks_total"]
    assert s2["inserted"] == 0
    assert s2["skipped_duplicates"] == s2["chunks_total"]

    async with session_factory() as session:
        count = (
            await session.execute(
                select(IPODocument).where(IPODocument.doc_type == "prospectus")
            )
        ).scalars().all()
        assert len(count) == s1["inserted"]


@pytest.mark.asyncio
async def test_run_ingest_prospectus_pdf_fetch_failure_returns_stage_fetch(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(url: str, **_kwargs: Any) -> bytes:
        raise pdf_loader.PDFFetchError("http_404")

    monkeypatch.setattr(pdf_loader, "fetch_pdf_bytes", fake_fetch)

    async with session_factory() as session:
        stats = await prospectus_ingest_service.run_ingest_prospectus(
            session,
            ipo_code="00002.HK",
            prospectus_url="https://x/y.pdf",
            settings=_make_settings(),
        )

    assert stats["stage"] == "fetch"
    assert stats["errors"] == 1
    assert stats["chunks_total"] == 0
    assert stats["inserted"] == 0


@pytest.mark.asyncio
async def test_run_ingest_prospectus_pdf_corrupt_returns_stage_extract(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(url: str, **_kwargs: Any) -> bytes:
        return b"this is not a real PDF, just garbage bytes"

    monkeypatch.setattr(pdf_loader, "fetch_pdf_bytes", fake_fetch)

    async with session_factory() as session:
        stats = await prospectus_ingest_service.run_ingest_prospectus(
            session,
            ipo_code="00003.HK",
            prospectus_url="https://x/corrupt.pdf",
            settings=_make_settings(),
        )

    assert stats["stage"] == "extract"
    assert stats["errors"] == 1
    assert stats["chunks_total"] == 0


@pytest.mark.asyncio
async def test_run_ingest_prospectus_embed_failure_returns_stage_embed(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = _build_pdf("Some content " * 10)

    async def fake_fetch(url: str, **_kwargs: Any) -> bytes:
        return pdf

    async def fake_embed_fail(texts: list[str], **_kwargs: Any) -> EmbeddingResult:
        raise LLMProviderError("upstream 503", provider="siliconflow", model="bge-m3")

    monkeypatch.setattr(pdf_loader, "fetch_pdf_bytes", fake_fetch)
    monkeypatch.setattr(prospectus_ingest_service, "embed", fake_embed_fail)

    async with session_factory() as session:
        stats = await prospectus_ingest_service.run_ingest_prospectus(
            session,
            ipo_code="00004.HK",
            prospectus_url="https://x/embed_fail.pdf",
            settings=_make_settings(),
        )

    assert stats["stage"] == "embed"
    assert stats["errors"] == 1
    assert stats["pdf_pages"] >= 1
    assert stats["chunks_total"] >= 1
    assert stats["chunks_embedded"] == 0
    assert stats["inserted"] == 0


@pytest.mark.asyncio
async def test_run_ingest_prospectus_orphan_when_no_ipo_row(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ipos`` 表无对应 code → ``ipo_id=NULL`` 但 chunk 仍正常入库."""
    pdf = _build_pdf("Orphan chunk content " * 20)

    async def fake_fetch(url: str, **_kwargs: Any) -> bytes:
        return pdf

    fake_embed, _ = _fake_embed_factory()
    monkeypatch.setattr(pdf_loader, "fetch_pdf_bytes", fake_fetch)
    monkeypatch.setattr(prospectus_ingest_service, "embed", fake_embed)

    async with session_factory() as session:
        stats = await prospectus_ingest_service.run_ingest_prospectus(
            session,
            ipo_code="99999.HK",
            prospectus_url="https://x/orphan.pdf",
            settings=_make_settings(),
        )
        await session.commit()

    assert stats["stage"] == "ok"
    assert stats["inserted"] >= 1

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(IPODocument).where(IPODocument.ipo_code == "99999.HK")
            )
        ).scalars().all()
        for r in rows:
            assert r.ipo_id is None
            assert r.ipo_code == "99999.HK"


@pytest.mark.asyncio
async def test_run_ingest_prospectus_embed_dim_mismatch_returns_stage_embed(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """embed 返回维度 ≠ settings.llm_embedding_dim 时拒绝入库 (防索引污染)."""
    pdf = _build_pdf("dim mismatch content " * 10)

    async def fake_fetch(url: str, **_kwargs: Any) -> bytes:
        return pdf

    fake_embed, _ = _fake_embed_factory(dim=512)  # 故意返回 512 维
    monkeypatch.setattr(pdf_loader, "fetch_pdf_bytes", fake_fetch)
    monkeypatch.setattr(prospectus_ingest_service, "embed", fake_embed)

    async with session_factory() as session:
        stats = await prospectus_ingest_service.run_ingest_prospectus(
            session,
            ipo_code="00005.HK",
            prospectus_url="https://x/dim.pdf",
            settings=_make_settings(),
        )

    assert stats["stage"] == "embed"
    assert stats["errors"] == 1
    assert stats["inserted"] == 0


@pytest.mark.asyncio
async def test_run_ingest_prospectus_resolves_ipo_id_when_present(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_ipo_documents: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ipos`` 表已有对应 ``code`` → ``ipo_id`` 自动 resolve, chunk FK 正确建立."""
    pdf = _build_pdf("Tencent IPO content " * 20)

    async def fake_fetch(url: str, **_kwargs: Any) -> bytes:
        return pdf

    fake_embed, _ = _fake_embed_factory()
    monkeypatch.setattr(pdf_loader, "fetch_pdf_bytes", fake_fetch)
    monkeypatch.setattr(prospectus_ingest_service, "embed", fake_embed)

    from app.schemas.ipo import IPOItem
    from app.services.ipo_ingest_service import upsert_ipos

    async with session_factory() as session:
        await upsert_ipos(
            session,
            [
                IPOItem(
                    code="03690.HK",
                    name="美团",
                    market="HK",
                    status="listed",
                )
            ],
        )
        await session.commit()
        existing_ipo_id = (
            await session.execute(select(IPO.ipo_id).where(IPO.code == "03690.HK"))
        ).scalar_one()

    async with session_factory() as session:
        stats = await prospectus_ingest_service.run_ingest_prospectus(
            session,
            ipo_code="03690.HK",
            prospectus_url="https://x/03690.pdf",
            settings=_make_settings(),
        )
        await session.commit()

    assert stats["stage"] == "ok"
    assert stats["inserted"] >= 1

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(IPODocument).where(IPODocument.ipo_code == "03690.HK")
            )
        ).scalars().all()
        for r in rows:
            assert r.ipo_id == existing_ipo_id
