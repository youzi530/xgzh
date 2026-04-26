"""BE-S2-000: ``hkex_client`` 单元测试.

覆盖 (8 条; spec/09 §AC 要求 ≥5 条):

A. ``parse_applicants_html`` 纯函数:
    1. happy: 标准 hkexnews 申请人页 HTML → 拿到 IPOItem + prospectus_urls 映射
    2. 空表: ``<tbody>`` 没行 → 返回空结果
    3. 没 ``<table>``: HTML 完全不是表格 → warning + 返回空
    4. 行无 PDF 链接 → 跳过这行, 不进 result
    5. 行公司名为空白 → 跳过

B. ``fetch_hk_applicants_with_client`` HTTP:
    6. 200 + 正常 HTML → ok
    7. HTTP 5xx → warning + 返回空 (不抛, 与 fetch_a_ipos 一致)
    8. ``httpx.RequestError`` (网络层挂) → warning + 返回空 (不抛)

每条都不依赖 PG / Redis (纯 in-memory 单元), 不需要 ``@pytest.mark.db``.
"""

from __future__ import annotations

from datetime import datetime
from textwrap import dedent

import httpx
import pytest
import respx

from app.adapters.hkex_client import (
    APPLICANTS_PATH,
    HKApplicantFetchResult,
    fetch_hk_applicants_with_client,
    parse_applicants_html,
)

# ─── 共享 fixture: 模拟 hkexnews 申请人页 HTML ─────────────────────────────


_HKEX_BASE_URL = "https://www1.hkexnews.hk"


def _applicants_html(rows_html: str) -> str:
    """构造 hkexnews 申请人页 HTML 模板; 列名走中文版.

    实际 hkexnews ``applicants_c.htm`` 表头列序是:
    [公司名称 | 建议上市的市场 | 提交日期 | 聆讯後資料集].
    """
    return dedent(
        f"""\
        <html>
        <head><meta charset="utf-8"><title>申请人</title></head>
        <body>
          <table class="applicants_listing">
            <thead>
              <tr>
                <th>公司名称</th>
                <th>建议上市的市场</th>
                <th>提交日期</th>
                <th>聆讯后資料集</th>
              </tr>
            </thead>
            <tbody>
              {rows_html}
            </tbody>
          </table>
        </body>
        </html>
        """
    )


# ─── A. parse_applicants_html ─────────────────────────────────────────────


def test_parse_happy_two_rows() -> None:
    """标准 happy: 2 行申请人 → 各 1 条 IPOItem 和 1 条 prospectus URL."""
    rows = """
    <tr>
      <td><a href="/listedco/listconews/sehk/2026/0301/2026030100123.pdf">利邦控股有限公司</a></td>
      <td>主板</td>
      <td>01/03/2026</td>
      <td><a href="/listedco/listconews/sehk/2026/0301/2026030100124.pdf">PHIP</a></td>
    </tr>
    <tr>
      <td><a href="/listedco/listconews/sehk/2026/0215/2026021500077.pdf">某大科技集团股份有限公司</a></td>
      <td>主板</td>
      <td>15/02/2026</td>
      <td>—</td>
    </tr>
    """
    result = parse_applicants_html(
        _applicants_html(rows), base_url=_HKEX_BASE_URL, limit=100
    )
    assert isinstance(result, HKApplicantFetchResult)
    assert len(result.items) == 2

    # 第 1 行: ASCII 名 → slug 走 ASCII 截断
    item1 = result.items[0]
    assert item1.name == "利邦控股有限公司"
    assert item1.market == "HK"
    assert item1.status == "upcoming"
    # 占位形态: AP{yymmdd}{slug:5}.HK = 16 字符 (卡 VARCHAR(16) 上限)
    assert item1.code.startswith("AP260301")
    assert item1.code.endswith(".HK")
    assert len(item1.code) == 16
    assert item1.subscribe_start == datetime(2026, 3, 1)
    assert item1.data_source.startswith("hkexnews-applicants")
    assert (
        result.prospectus_urls[item1.code]
        == "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0301/2026030100123.pdf"
    )

    # 第 2 行: 全中文公司名 → slug 用 sha1 兜底, 长度 5, 大写 hex
    item2 = result.items[1]
    assert item2.name == "某大科技集团股份有限公司"
    assert item2.code.startswith("AP260215")
    slug2 = item2.code.removeprefix("AP260215").removesuffix(".HK")
    assert len(slug2) == 5
    assert all(c in "0123456789ABCDEF" for c in slug2)


def test_parse_empty_tbody_returns_empty() -> None:
    """``<tbody>`` 无任何 ``<tr>`` → 空结果, 不抛."""
    result = parse_applicants_html(
        _applicants_html(""), base_url=_HKEX_BASE_URL, limit=100
    )
    assert result.items == []
    assert result.prospectus_urls == {}


def test_parse_no_table_returns_empty() -> None:
    """整个页面没 ``<table>`` → warning + 空 (不抛)."""
    html = "<html><body><h1>maintenance</h1><p>page is being updated</p></body></html>"
    result = parse_applicants_html(html, base_url=_HKEX_BASE_URL, limit=100)
    assert result.items == []
    assert result.prospectus_urls == {}


def test_parse_row_without_pdf_link_is_skipped() -> None:
    """行公司名栏没 ``.pdf`` href → 跳过这行 (没 PDF URL 喂不了 BE-S2-004)."""
    rows = """
    <tr>
      <td>某不带 PDF 的公司</td>
      <td>主板</td>
      <td>01/03/2026</td>
      <td>PHIP</td>
    </tr>
    <tr>
      <td><a href="/path/proof.pdf">某有 PDF 的公司</a></td>
      <td>主板</td>
      <td>02/03/2026</td>
      <td>PHIP</td>
    </tr>
    """
    result = parse_applicants_html(
        _applicants_html(rows), base_url=_HKEX_BASE_URL, limit=100
    )
    assert len(result.items) == 1
    assert result.items[0].name == "某有 PDF 的公司"


def test_parse_row_with_blank_name_is_skipped() -> None:
    """公司名栏文字为空 → 跳过 (hkexnews 偶尔有占位空行)."""
    rows = """
    <tr>
      <td><a href="/path/blank.pdf">   </a></td>
      <td>主板</td>
      <td>01/03/2026</td>
      <td>PHIP</td>
    </tr>
    <tr>
      <td><a href="/path/real.pdf">真实公司</a></td>
      <td>主板</td>
      <td>02/03/2026</td>
      <td>PHIP</td>
    </tr>
    """
    result = parse_applicants_html(
        _applicants_html(rows), base_url=_HKEX_BASE_URL, limit=100
    )
    assert len(result.items) == 1
    assert result.items[0].name == "真实公司"


def test_parse_limit_truncates_results() -> None:
    """``limit=2`` 时最多返回 2 条, 即便页面有 3 条."""
    rows_html = "".join(
        f"""
    <tr>
      <td><a href="/p/{i}.pdf">公司 {i}</a></td>
      <td>主板</td>
      <td>0{i}/03/2026</td>
      <td>PHIP</td>
    </tr>
    """
        for i in range(1, 4)
    )
    result = parse_applicants_html(
        _applicants_html(rows_html), base_url=_HKEX_BASE_URL, limit=2
    )
    assert len(result.items) == 2


# ─── B. fetch_hk_applicants_with_client (HTTP layer) ──────────────────────


@pytest.mark.asyncio
@respx.mock(base_url=_HKEX_BASE_URL)
async def test_fetch_with_client_happy(respx_mock: respx.Router) -> None:
    """200 + 正常 HTML → 返回非空."""
    rows = """
    <tr>
      <td><a href="/listedco/listconews/sehk/2026/0420/2026042000088.pdf">利邦控股有限公司</a></td>
      <td>主板</td>
      <td>20/04/2026</td>
      <td>PHIP</td>
    </tr>
    """
    respx_mock.get(APPLICANTS_PATH).mock(
        return_value=httpx.Response(200, text=_applicants_html(rows))
    )

    async with httpx.AsyncClient(base_url=_HKEX_BASE_URL) as client:
        result = await fetch_hk_applicants_with_client(
            client, base_url=_HKEX_BASE_URL, limit=10, request_timeout=5.0
        )

    assert len(result.items) == 1
    assert result.items[0].name == "利邦控股有限公司"
    assert result.items[0].code.startswith("AP260420")
    assert len(result.items[0].code) == 16


@pytest.mark.asyncio
@respx.mock(base_url=_HKEX_BASE_URL)
async def test_fetch_with_client_5xx_returns_empty(respx_mock: respx.Router) -> None:
    """upstream 5xx → 返回空, 不抛 (上游 scheduler/service 不需 try/except)."""
    respx_mock.get(APPLICANTS_PATH).mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient(base_url=_HKEX_BASE_URL) as client:
        result = await fetch_hk_applicants_with_client(
            client, base_url=_HKEX_BASE_URL, limit=10, request_timeout=5.0
        )

    assert result.items == []


@pytest.mark.asyncio
@respx.mock(base_url=_HKEX_BASE_URL)
async def test_fetch_with_client_network_error_returns_empty(
    respx_mock: respx.Router,
) -> None:
    """``httpx.ConnectError`` 模拟网络层挂 → 空结果 (不抛)."""
    respx_mock.get(APPLICANTS_PATH).mock(side_effect=httpx.ConnectError("DNS down"))

    async with httpx.AsyncClient(base_url=_HKEX_BASE_URL) as client:
        result = await fetch_hk_applicants_with_client(
            client, base_url=_HKEX_BASE_URL, limit=10, request_timeout=5.0
        )

    assert result.items == []
