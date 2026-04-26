"""BE-S2-004 — PDF 下载 + 解析单测.

只测 ``app/adapters/pdf_loader.py`` 这一层:
- ``fetch_pdf_bytes_with_client``: respx mock httpx
- ``extract_text_per_page``: 用一个手工构造的 minimal PDF 1.4 字节流, pypdf 直接消化

为什么自构 PDF 而非加 reportlab/fpdf2 测试依赖
=================================================
- 项目走精简包路线 (spec/06), 装一个写 PDF 的库太贵
- pypdf 自身的 ``PdfWriter`` 只支持 UTF-16BE TextStringObject, 提取出来字节
  顺序错乱 (Helvetica 没 ToUnicode CMap), 不适合断言文本内容
- minimal PDF 1.4 ASCII 流 + ``Helvetica`` Type1 标准字体 + ``Tj`` 操作符,
  pypdf ``extract_text`` 能直接还原成 ASCII 字符串. 60 行 fixture 即可
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters.pdf_loader import (
    PDFExtractResult,
    PDFFetchError,
    extract_text_per_page,
    fetch_pdf_bytes_with_client,
)

_HOST = "https://www1.hkexnews.hk"
_PDF_PATH = "/listedco/listconews/sehk/2026/0301/2026030100123.pdf"


# ─── PDF fixture builder ────────────────────────────────────────────────────


def _build_minimal_pdf(*pages_text: str) -> bytes:
    """生成 N 页 PDF, 每页文本对应 ``pages_text[i]`` (ASCII).

    用 PDFDocEncoding 直接 ``Tj`` (Helvetica Type1), pypdf 提取为正常字符串.
    手写 xref + trailer 比引 reportlab 更轻.
    """
    objs: list[bytes] = []
    page_obj_ids = list(range(3, 3 + len(pages_text)))
    content_obj_ids = list(range(3 + len(pages_text), 3 + 2 * len(pages_text)))
    font_obj_id = 3 + 2 * len(pages_text)

    # 1: catalog
    objs.append(b"1 0 obj\n<</Type /Catalog /Pages 2 0 R>>\nendobj")
    # 2: pages
    kids = " ".join(f"{i} 0 R" for i in page_obj_ids)
    objs.append(
        f"2 0 obj\n<</Type /Pages /Kids [{kids}] /Count {len(pages_text)}>>\nendobj".encode()
    )

    for page_id, content_id, _text in zip(page_obj_ids, content_obj_ids, pages_text, strict=True):
        objs.append(
            (
                f"{page_id} 0 obj\n<</Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 612 792] /Contents {content_id} 0 R "
                f"/Resources <</Font <</F1 {font_obj_id} 0 R>>>>>>\nendobj"
            ).encode()
        )

    for content_id, text in zip(content_obj_ids, pages_text, strict=True):
        # escape any '(' or ')' inside text (PDF literal syntax)
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 50 750 Td ({safe}) Tj ET".encode()
        objs.append(

                f"{content_id} 0 obj\n<</Length {len(stream)}>>\nstream\n".encode()
                + stream
                + b"\nendstream\nendobj"

        )

    # font
    objs.append(
        (
            f"{font_obj_id} 0 obj\n<</Type /Font /Subtype /Type1 "
            f"/BaseFont /Helvetica>>\nendobj"
        ).encode()
    )

    header = b"%PDF-1.4\n"
    body = b""
    offsets: list[int] = []
    pos = len(header)
    for obj in objs:
        offsets.append(pos)
        body += obj + b"\n"
        pos += len(obj) + 1

    n_objs = len(objs)
    xref_pos = len(header) + len(body)
    xref = f"xref\n0 {n_objs + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()

    trailer = (
        f"trailer\n<</Size {n_objs + 1} /Root 1 0 R>>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()

    return header + body + xref + trailer


# ─── extract_text_per_page ─────────────────────────────────────────────────


def test_extract_single_page_happy() -> None:
    pdf = _build_minimal_pdf("Hello World prospectus")
    result = extract_text_per_page(pdf)
    assert isinstance(result, PDFExtractResult)
    assert result.total_pages == 1
    assert result.extracted_pages == 1
    assert result.pages[0][0] == 1
    assert "Hello World prospectus" in result.pages[0][1]


def test_extract_multi_page_preserves_order() -> None:
    pdf = _build_minimal_pdf(
        "Page one summary",
        "Page two financials",
        "Page three risks",
    )
    result = extract_text_per_page(pdf)
    assert result.total_pages == 3
    assert [p for p, _ in result.pages] == [1, 2, 3]
    assert "summary" in result.pages[0][1]
    assert "financials" in result.pages[1][1]
    assert "risks" in result.pages[2][1]


def test_extract_empty_bytes_raises() -> None:
    with pytest.raises(PDFFetchError, match="empty"):
        extract_text_per_page(b"")


def test_extract_corrupt_bytes_raises_pdfetcherror() -> None:
    with pytest.raises(PDFFetchError, match="reader_init_failed"):
        extract_text_per_page(b"not a real pdf, just garbage")


# ─── fetch_pdf_bytes_with_client ───────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock(base_url=_HOST)
async def test_fetch_with_client_happy(respx_mock: respx.MockRouter) -> None:
    pdf = _build_minimal_pdf("Real PDF content")
    respx_mock.get(_PDF_PATH).mock(
        return_value=httpx.Response(
            200,
            content=pdf,
            headers={"content-type": "application/pdf"},
        )
    )

    async with httpx.AsyncClient(base_url=_HOST) as client:
        result = await fetch_pdf_bytes_with_client(
            client, _PDF_PATH, max_size_mb=10
        )
    assert result == pdf


@pytest.mark.asyncio
@respx.mock(base_url=_HOST)
async def test_fetch_with_client_404_raises_pdfetcherror(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(_PDF_PATH).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient(base_url=_HOST) as client:
        with pytest.raises(PDFFetchError, match="http_404"):
            await fetch_pdf_bytes_with_client(client, _PDF_PATH, max_size_mb=10)


@pytest.mark.asyncio
@respx.mock(base_url=_HOST)
async def test_fetch_with_client_oversized_content_length(
    respx_mock: respx.MockRouter,
) -> None:
    """Content-Length 大于上限时, 流式读之前就 reject."""
    big = b"x" * (2 * 1024 * 1024)
    respx_mock.get(_PDF_PATH).mock(
        return_value=httpx.Response(
            200,
            content=big,
            headers={"content-length": str(len(big))},
        )
    )
    async with httpx.AsyncClient(base_url=_HOST) as client:
        with pytest.raises(PDFFetchError, match="oversized"):
            await fetch_pdf_bytes_with_client(client, _PDF_PATH, max_size_mb=1)


@pytest.mark.asyncio
@respx.mock(base_url=_HOST)
async def test_fetch_with_client_oversized_streamed_no_content_length(
    respx_mock: respx.MockRouter,
) -> None:
    """无 Content-Length 时也要在累计字节超限时 reject (防对端不诚实)."""
    big = b"y" * (2 * 1024 * 1024)
    respx_mock.get(_PDF_PATH).mock(
        return_value=httpx.Response(
            200,
            content=big,
        )
    )
    async with httpx.AsyncClient(base_url=_HOST) as client:
        with pytest.raises(PDFFetchError, match="oversized"):
            await fetch_pdf_bytes_with_client(client, _PDF_PATH, max_size_mb=1)


@pytest.mark.asyncio
@respx.mock(base_url=_HOST)
async def test_fetch_with_client_network_error(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(_PDF_PATH).mock(
        side_effect=httpx.ConnectError("DNS fail")
    )
    async with httpx.AsyncClient(base_url=_HOST) as client:
        with pytest.raises(PDFFetchError, match="http_error"):
            await fetch_pdf_bytes_with_client(client, _PDF_PATH, max_size_mb=10)
