"""招股书入库编排 (BE-S2-004 RAG 流水线第 3 层).

下载 → 解析 → 切分 → 批量 embed → upsert ``ipo_documents``,
失败一律 logger.exception + 返回 stats, 不抛 (与 BE-007 / BE-S2-000 调度任务行为
对齐, 让上层 scheduler / Tool 调用方拿到结构化进度而非异常分支).

入库幂等键 (BE-S2-003 落地)
============================
- ``doc_id``: ``sha256(prospectus_url)`` 前 32 hex (固定 32 字符 ≤ String(64))
  → 同一份招股书 (URL 不变) 的所有 chunk 共享 ``doc_id``
- ``content_hash``: ``sha256(chunk_text)`` 64 hex
- partial UNIQUE ``(doc_id, content_hash) WHERE content_hash IS NOT NULL``
  保证: 重抓时同 chunk 不重复 INSERT (``ON CONFLICT DO NOTHING``);
  招股书改版 (URL 变 → doc_id 变) 时新版本与旧版本独立共存

不在本 PR 做的事 (P1+)
=======================
- A 股招股书入库: 监管反爬 + 经常被改版 + 合规争议 (spec/09 §不做)
- 把这个 service 挂到 APScheduler: 招股书 PDF 几十 MB × N 只新股, lifespan
  startup 自动跑会撑爆带宽 / 临时盘. 改成 ``run_ingest_pending_prospectuses``
  + Sprint 3 给运营手动触发 + 速率控制
- 表格 / 财务摘要抽取: pypdf 不擅长 table; Sprint 3 财务模块单独建 schema +
  pdfplumber 兜底
- 多语言切分策略调优: 当前简单按 CJK 字符数估 token, 等 BE-S2-009 评测
  baseline 出来再回头看是否要给 HK 英文版招股书走单独的 chunk_size

stats 字段
==========
- ``pdf_pages``: PDF 总页数
- ``extracted_pages``: pypdf 实际抽出非空文本的页数
- ``chunks_total``: 切分后的 chunk 总数 (含 dedup 前)
- ``chunks_embedded``: 成功送 embed 的 chunk 数
- ``inserted``: 真正落库的新 chunk 数
- ``skipped_duplicates``: ON CONFLICT DO NOTHING 命中数 = chunks_embedded - inserted
- ``errors``: 阶段失败计数 (download / extract / chunk / embed / db 任一阶段 +1)
- ``stage``: 失败时定位到哪一阶段, 成功时 "ok"
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, cast

from sqlalchemy import Table, select
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import pdf_loader
from app.adapters.llm_client import LLMError, embed
from app.adapters.pdf_loader import PDFFetchError
from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.db.models import IPO, IPODocument
from app.services.rag.chunker import Chunk, split_text


def _empty_stats() -> dict[str, Any]:
    return {
        "pdf_pages": 0,
        "extracted_pages": 0,
        "chunks_total": 0,
        "chunks_embedded": 0,
        "inserted": 0,
        "skipped_duplicates": 0,
        "errors": 0,
        "stage": "ok",
    }


def _doc_id_from_url(url: str) -> str:
    """``sha256(url)`` 前 32 hex (与 ``ipo_documents.doc_id String(64)`` 兼容).

    URL 变了就视作新文档; 旧 chunk 不会被自动清理 (留作历史轨迹).
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def _content_hash(text: str) -> str:
    """整 64 hex sha256, 配 ``ipo_documents.content_hash CHAR(64)``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _locate_chunk_page(chunk: Chunk, page_offsets: list[tuple[int, int]]) -> int | None:
    """根据 chunk 的 char_start 找其首字符所在的 page no (1-based).

    ``page_offsets[i] = (page_no, abs_char_start_of_page_in_combined_text)``.
    """
    if not page_offsets:
        return None
    target = chunk.char_start
    last_page: int | None = None
    for page_no, page_start in page_offsets:
        if page_start <= target:
            last_page = page_no
        else:
            break
    return last_page


async def run_ingest_prospectus(
    session: AsyncSession,
    *,
    ipo_code: str,
    prospectus_url: str,
    ipo_id: uuid.UUID | None = None,
    lang: str = "zh",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """对一份招股书走完整入库流水线.

    ``ipo_id`` 不传时尝试从 ``ipos`` 表按 ``code=ipo_code`` 拿; 拿不到不抛,
    走"无主孤儿 chunk"路径 (``ipo_id=NULL``), 后续 BE-S2-005 检索时仍可用
    ``ipo_code`` 过滤.

    raise 策略: 全部 ``logger.exception`` + 计 stats.errors, **不抛**;
    上层 (Tool / 评测脚本) 直接读 ``stats["stage"]`` 决定 retry 策略.

    本函数自己不 commit/rollback; 由调用方控制事务边界. 设计原因:
    1. 测试要在事务里跑 + 测完回滚, ``conftest`` 已是这套约定
    2. 上层若多份 PDF 批量入库, 可控制是单份独立事务还是合并提交
    """
    settings = settings or get_settings()
    stats = _empty_stats()

    # ── stage 1: 下载 PDF ──────────────────────────────────────────────────
    try:
        pdf_bytes = await pdf_loader.fetch_pdf_bytes(
            prospectus_url,
            max_size_mb=settings.pdf_max_size_mb,
            request_timeout=settings.pdf_request_timeout_seconds,
        )
    except PDFFetchError as e:
        logger.warning(f"prospectus_ingest.fetch_failed url={prospectus_url}: {e}")
        stats["errors"] = 1
        stats["stage"] = "fetch"
        return stats
    except Exception as e:
        logger.exception(f"prospectus_ingest.fetch_unexpected url={prospectus_url}: {e}")
        stats["errors"] = 1
        stats["stage"] = "fetch"
        return stats

    # ── stage 2: 抽页文 ────────────────────────────────────────────────────
    try:
        extract_result = pdf_loader.extract_text_per_page(pdf_bytes)
    except PDFFetchError as e:
        logger.warning(f"prospectus_ingest.extract_failed url={prospectus_url}: {e}")
        stats["errors"] = 1
        stats["stage"] = "extract"
        return stats

    stats["pdf_pages"] = extract_result.total_pages
    stats["extracted_pages"] = extract_result.extracted_pages

    # ── stage 3: 拼全文 + 记录页偏移 + 切分 ────────────────────────────────
    full_chunks: list[Chunk] = []
    full_text_parts: list[str] = []
    page_offsets: list[tuple[int, int]] = []
    cursor = 0
    for pno, page_text in extract_result.pages:
        page_offsets.append((pno, cursor))
        full_text_parts.append(page_text)
        cursor += len(page_text) + 2

    full_text = "\n\n".join(full_text_parts)
    try:
        full_chunks = split_text(
            full_text,
            max_tokens=settings.rag_chunk_size_tokens,
            overlap_tokens=settings.rag_chunk_overlap_tokens,
        )
    except Exception as e:
        logger.exception(f"prospectus_ingest.chunk_failed url={prospectus_url}: {e}")
        stats["errors"] = 1
        stats["stage"] = "chunk"
        return stats

    stats["chunks_total"] = len(full_chunks)
    if not full_chunks:
        logger.warning(
            f"prospectus_ingest.chunk_empty url={prospectus_url} pages={extract_result.extracted_pages}"
        )
        return stats

    # ── stage 4: 批量 embed ────────────────────────────────────────────────
    try:
        emb_result = await embed([c.text for c in full_chunks])
    except LLMError as e:
        logger.warning(f"prospectus_ingest.embed_failed url={prospectus_url}: {e}")
        stats["errors"] = 1
        stats["stage"] = "embed"
        return stats
    except Exception as e:
        logger.exception(f"prospectus_ingest.embed_unexpected url={prospectus_url}: {e}")
        stats["errors"] = 1
        stats["stage"] = "embed"
        return stats

    if emb_result.dim != settings.llm_embedding_dim:
        logger.error(
            f"prospectus_ingest.embed_dim_mismatch got={emb_result.dim} "
            f"expect={settings.llm_embedding_dim} url={prospectus_url}"
        )
        stats["errors"] = 1
        stats["stage"] = "embed"
        return stats

    stats["chunks_embedded"] = len(emb_result.embeddings)

    # ── stage 5: 入库 (ON CONFLICT DO NOTHING 走 BE-S2-003 partial UNIQUE) ─
    if ipo_id is None:
        ipo_id = await _resolve_ipo_id_by_code(session, ipo_code)

    doc_id = _doc_id_from_url(prospectus_url)
    rows: list[dict[str, Any]] = []
    for idx, (chunk, emb_vec) in enumerate(
        zip(full_chunks, emb_result.embeddings, strict=True)
    ):
        page_no: int | None = _locate_chunk_page(chunk, page_offsets)
        rows.append(
            {
                "ipo_id": ipo_id,
                "ipo_code": ipo_code,
                "doc_id": doc_id,
                "doc_type": "prospectus",
                "page": page_no,
                "text": chunk.text,
                "embedding": emb_vec,
                "chunk_index": idx,
                "token_count": chunk.token_count,
                "content_hash": _content_hash(chunk.text),
                "embedding_model": emb_result.model,
                "embedding_dim": emb_result.dim,
                "lang": lang,
            }
        )

    try:
        stmt = pg_insert(cast(Table, IPODocument.__table__)).values(rows)
        # 走 BE-S2-003 partial UNIQUE 索引: (doc_id, content_hash) WHERE content_hash
        # IS NOT NULL. PG 要求 partial 索引在 ON CONFLICT 时必须给出同样的谓词
        # ``index_where`` (否则 InvalidColumnReferenceError "no constraint matching").
        upsert = stmt.on_conflict_do_nothing(
            index_elements=["doc_id", "content_hash"],
            index_where=sa_text("content_hash IS NOT NULL"),
        )
        result = await session.execute(upsert)
        await session.flush()
    except Exception as e:
        logger.exception(
            f"prospectus_ingest.db_failed url={prospectus_url} chunks={len(rows)}: {e}"
        )
        stats["errors"] = 1
        stats["stage"] = "db"
        return stats

    # ``Result.rowcount`` 在 SQLAlchemy 2.0 是 attribute, mypy stubs 漏标 → cast.
    inserted = cast(int, getattr(result, "rowcount", 0) or 0)
    stats["inserted"] = inserted
    stats["skipped_duplicates"] = max(0, len(rows) - inserted)

    logger.info(
        f"prospectus_ingest.ok code={ipo_code} doc_id={doc_id} "
        f"pages={extract_result.extracted_pages}/{extract_result.total_pages} "
        f"chunks={len(rows)} inserted={inserted} "
        f"dup={stats['skipped_duplicates']}"
    )
    return stats


async def _resolve_ipo_id_by_code(
    session: AsyncSession, ipo_code: str
) -> uuid.UUID | None:
    """按 ``code`` 查 ``ipo_id`` (HK / A 都行); 同 code 多 market 时取第一个.

    实际部署里同 code 只会一个 (``(code, market)`` 才唯一), 但 schema 上不强约束.
    DB 没数据 (冷启动) 时返回 None, 让 chunk 以 ``ipo_id=NULL`` 入库, 不阻塞流水线.
    """
    q = select(IPO.ipo_id).where(IPO.code == ipo_code).limit(1)
    row = (await session.execute(q)).first()
    if row is None:
        return None
    return cast(uuid.UUID, row[0])


__all__ = [
    "run_ingest_prospectus",
]
