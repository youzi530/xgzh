"""PDF 下载 + 解析 (BE-S2-004 招股书 RAG 流水线第 1 层).

职责
====
1. ``fetch_pdf_bytes(url, max_size_mb, timeout)`` 从远程下载 PDF 字节流;
   带尺寸上限 (防 OOM) + HTTP 超时 + 4xx/5xx 错误归一化.
2. ``extract_text_per_page(pdf_bytes)`` 用 ``pypdf`` 抽页文本, 输出 ``[(page, text), ...]``
   元组列表; 失败/空页过滤掉.

为什么单独立这一层
==================
- 让 ``services/rag/prospectus_ingest_service`` 只关心"切分 + embed + 入库"
  的业务编排, 不掺 IO 错误处理细节
- 单测只 mock ``httpx`` 一个层, 不需要 monkeypatch ``pypdf`` (pypdf 处理纯
  bytes 即可, 可控易测)
- BE-S2-005 / BE-S2-009 评测脚本若要离线跑(本地拷贝 PDF), 直接调
  ``extract_text_per_page(open(...).read())`` 绕过下载层

为什么不上 ``pdfplumber`` / ``pdfminer``
=========================================
- 招股书是数字化 PDF (无 OCR 需求); pypdf 6.x 文本抽取够用 + 纯 Python
- pdfplumber 强在 form/table 识别, 当前 RAG 不做表抽取 (Sprint 3+ 财务数据
  另立 schema), 增量包体 + 依赖链复杂度不划算
- pypdf 6.x 修复了多年的 PDF 1.7 解析锐角问题, 当下默认推荐 (changelog ref:
  pypdf v5 release notes 提到稳定 API)

错误处理
========
所有失败抛 ``PDFFetchError`` (子类自 Exception), 调用方决定是抛是吞
(本 PR ``prospectus_ingest_service`` 选择捕获 + logger.exception + 计入 stats).
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

import httpx
from pypdf import PdfReader

logger = logging.getLogger(__name__)


class PDFFetchError(Exception):
    """PDF 下载 / 解析阶段所有错误的统一基类."""


@dataclass(frozen=True, slots=True)
class PDFExtractResult:
    """提取结果. 同时给上层算 page-text 总长度 / 命中率."""

    pages: list[tuple[int, str]] = field(default_factory=list)
    total_pages: int = 0
    extracted_pages: int = 0

    @property
    def total_chars(self) -> int:
        return sum(len(t) for _, t in self.pages)


# ─── 下载 ───────────────────────────────────────────────────────────────────


async def fetch_pdf_bytes_with_client(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_size_mb: int,
) -> bytes:
    """走外部传入的 ``httpx.AsyncClient`` 下载 PDF.

    分两层 (这里接 client / 外层 ``fetch_pdf_bytes`` 自建) 同 ``hkex_client``,
    让单测可以 ``respx_mock`` 拦请求, 不必 monkeypatch httpx.

    尺寸上限走两道:
    1. 看 ``Content-Length`` header 提前拒 (绝大多数 hkexnews 招股书会带);
    2. 流式读 + 累加字节数, 防服务端不给 Content-Length 时一直读爆内存.

    raises: ``PDFFetchError``
    """
    max_bytes = max_size_mb * 1024 * 1024
    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise PDFFetchError(
                    f"pdf.fetch http_{resp.status_code} url={url}"
                )

            content_length = resp.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > max_bytes:
                        raise PDFFetchError(
                            f"pdf.fetch oversized content_length={content_length} "
                            f"limit={max_bytes} url={url}"
                        )
                except ValueError:
                    pass

            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise PDFFetchError(
                        f"pdf.fetch oversized streamed={len(buf)} "
                        f"limit={max_bytes} url={url}"
                    )
            return bytes(buf)
    except PDFFetchError:
        raise
    except httpx.HTTPError as e:
        raise PDFFetchError(f"pdf.fetch http_error url={url}: {e}") from e
    except Exception as e:
        raise PDFFetchError(f"pdf.fetch unexpected url={url}: {e}") from e


async def fetch_pdf_bytes(
    url: str,
    *,
    max_size_mb: int = 50,
    request_timeout: float = 60.0,
) -> bytes:
    """对外入口: 下载招股书 PDF.

    自建 ``httpx.AsyncClient`` (User-Agent 显式带项目名, 让 hkexnews 风控可追溯).
    """
    headers = {
        "User-Agent": "xgzh-api/0.1 (+https://xgzh.example.com; contact: ops@xgzh)",
        "Accept": "application/pdf,*/*",
    }
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=request_timeout,
    ) as client:
        return await fetch_pdf_bytes_with_client(
            client, url, max_size_mb=max_size_mb
        )


# ─── 文本抽取 ──────────────────────────────────────────────────────────────


def extract_text_per_page(pdf_bytes: bytes) -> PDFExtractResult:
    """从 PDF 字节流抽页文本.

    返回 ``PDFExtractResult.pages = [(page_no, text), ...]`` (1-based page),
    自动过滤掉空页 / 抽取失败的页. 整体解析失败抛 ``PDFFetchError``.

    设计选择
    --------
    - 1-based page no: 与读者视觉一致 (招股书目录里写 "page 35" 就是第 35 页).
      bge-m3 input 加 page metadata 时也对得上.
    - 单页抽失败不让整本失败: 招股书 200+ 页, 偶尔遇到加密页 / 损坏页,
      logger.warning 后跳过即可.
    - ``extract_text("")`` 返回空字符串很常见 (扫描页 / 图片页), 这种 chunk
      没意义 → 在切分前直接过滤.
    """
    if not pdf_bytes:
        raise PDFFetchError("pdf.extract empty bytes")

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as e:
        raise PDFFetchError(f"pdf.extract reader_init_failed: {e}") from e

    total_pages = len(reader.pages)
    out: list[tuple[int, str]] = []
    for idx, page in enumerate(reader.pages, start=1):
        try:
            txt = page.extract_text() or ""
        except Exception as e:
            logger.warning(f"pdf.extract page_failed page={idx}: {e}")
            continue
        txt = txt.strip()
        if not txt:
            continue
        out.append((idx, txt))

    if not out:
        raise PDFFetchError(
            f"pdf.extract all_pages_empty total={total_pages} "
            f"(扫描版 PDF? 当前不支持 OCR)"
        )

    return PDFExtractResult(
        pages=out,
        total_pages=total_pages,
        extracted_pages=len(out),
    )


__all__: list[str] = [
    "PDFFetchError",
    "PDFExtractResult",
    "fetch_pdf_bytes",
    "fetch_pdf_bytes_with_client",
    "extract_text_per_page",
]
