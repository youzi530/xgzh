"""BUG-S6.7-003: ``aastocks_ipo_client`` 单元测试.

覆盖:

A. ``parse_aastocks_upcoming_html`` 纯函数:
    1. happy 2 行 fixture (天星医疗 / 可孚医疗) → 拿到 2 条 IPOItem
    2. 第 1 列 ``<a>名称</a><br/><span>代号</span>`` 拼接 → 正确切出 (name, code)
    3. ``"N/A"`` 招股价 → ``issue_price=None`` (可孚案例)
    4. 数值招股价 ``"98.5"`` → Decimal
    5. ``"2026/04/29"`` 日期 → ``date``
    6. status 推断: subscribe_end == today → "subscribing"
    7. status 推断: subscribe_end < today < listing_date → "upcoming"
    8. status 推断: listing_date <= today → "listed"
    9. 找不到 IPO table (HTML 改版) → 空结果 + 不抛
    10. 单行损坏 (列数 < 9) → skip + 不影响其它行

B. ``fetch_aastocks_upcoming_with_client`` HTTP:
    11. HTTP 5xx → 空结果
    12. body < 1024 byte (反爬退化) → 空结果
    13. happy → 解析 OK

不依赖 PG / Redis.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters.aastocks_ipo_client import (
    AASTOCKS_UPCOMING_URL,
    fetch_aastocks_upcoming_with_client,
    parse_aastocks_upcoming_html,
)

_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "aastocks_upcomingipo_sample.html"
)

# fixture 来源: 2026-04-29 抓的 upcomingipo.aspx
# 天星 sub_end=2026-04-29 listing=2026-05-05 — today=2026-04-29 → subscribing
# 可孚 sub_end=2026-04-30 listing=2026-05-06 — today=2026-04-29 → subscribing
_TODAY = date(2026, 4, 29)


@pytest.fixture
def sample_html() -> str:
    return _FIXTURE_PATH.read_text(encoding="utf-8")


# ─── A. parser 纯函数 ──────────────────────────────────────────


def test_parse_happy_2_rows(sample_html: str) -> None:
    """fixture 2 行 HTML → 2 条 IPOItem, code/name/status 都对."""
    result = parse_aastocks_upcoming_html(sample_html, today=_TODAY)
    assert len(result.items) == 2

    codes = [it.code for it in result.items]
    assert codes == ["01609.HK", "01187.HK"]

    names = [it.name for it in result.items]
    assert names == ["天星医疗", "可孚医疗"]

    industries = [it.industry for it in result.items]
    assert all(i == "医疗保健设备" for i in industries)


def test_parse_namecode_split(sample_html: str) -> None:
    """``"天星医疗01609.HK"`` 拼接体 → 切分正确."""
    result = parse_aastocks_upcoming_html(sample_html, today=_TODAY)
    tianxing = next(it for it in result.items if it.code == "01609.HK")
    assert tianxing.name == "天星医疗"
    # 不能名字里残留代号
    assert "01609" not in tianxing.name
    assert ".HK" not in tianxing.name


def test_parse_na_price_returns_none(sample_html: str) -> None:
    """可孚医疗招股价是 ``"N/A"`` → ``issue_price=None``."""
    result = parse_aastocks_upcoming_html(sample_html, today=_TODAY)
    kefu = next(it for it in result.items if it.code == "01187.HK")
    assert kefu.issue_price is None


def test_parse_numeric_price(sample_html: str) -> None:
    """天星医疗招股价 ``"98.5"`` → ``Decimal('98.5')``."""
    result = parse_aastocks_upcoming_html(sample_html, today=_TODAY)
    tianxing = next(it for it in result.items if it.code == "01609.HK")
    assert tianxing.issue_price == Decimal("98.5")


def test_parse_slash_dates(sample_html: str) -> None:
    """``"2026/04/29"`` → date(2026, 4, 29) (slash 而非 dash 分隔)."""
    result = parse_aastocks_upcoming_html(sample_html, today=_TODAY)
    tianxing = next(it for it in result.items if it.code == "01609.HK")
    assert tianxing.subscribe_end is not None
    assert tianxing.subscribe_end.date() == date(2026, 4, 29)
    assert tianxing.listing_date == date(2026, 5, 5)


def test_status_subscribing_at_end_today(sample_html: str) -> None:
    """today == subscribe_end → status='subscribing' (含截止当天)."""
    result = parse_aastocks_upcoming_html(sample_html, today=date(2026, 4, 29))
    tianxing = next(it for it in result.items if it.code == "01609.HK")
    # 天星 sub_end=2026-04-29
    assert tianxing.status == "subscribing"


def test_status_upcoming_after_subscribe_end(sample_html: str) -> None:
    """sub_end < today < listing_date → 'upcoming' (招股结束等上市)."""
    # 天星 sub_end=2026-04-29 listing=2026-05-05; today 取 5/3 应该 upcoming
    result = parse_aastocks_upcoming_html(sample_html, today=date(2026, 5, 3))
    tianxing = next(it for it in result.items if it.code == "01609.HK")
    assert tianxing.status == "upcoming"


def test_status_listed_when_today_past_listing(sample_html: str) -> None:
    """today >= listing_date → 'listed' (兜底分支)."""
    # 天星 listing=2026-05-05; today 取 5/10 应该 listed
    result = parse_aastocks_upcoming_html(sample_html, today=date(2026, 5, 10))
    tianxing = next(it for it in result.items if it.code == "01609.HK")
    assert tianxing.status == "listed"


def test_parse_no_ipo_table_returns_empty() -> None:
    """HTML 改版 (没"招股截止日"列) → 空结果, 不抛异常."""
    html = "<html><body><table><tr><th>foo</th><th>bar</th></tr></table></body></html>"
    result = parse_aastocks_upcoming_html(html, today=_TODAY)
    assert result.items == []


def test_parse_corrupt_row_skipped() -> None:
    """单行列数不足 9 → skip, 其它正常行不受影响."""
    html = """<html><body>
    <table>
      <tr><th>序号</th><th>公司</th><th>行业</th><th>招股价</th>
          <th>每手</th><th>入场费</th><th>招股截止日</th>
          <th>暗盘</th><th>上市日期</th></tr>
      <tr><td></td><td>broken</td></tr>  <!-- 损坏行: 2 cells -->
      <tr><td></td><td>天星医疗01609.HK</td><td>医疗</td><td>98.5</td>
          <td>50</td><td>4974</td><td>2026/04/29</td>
          <td>2026/05/04</td><td>2026/05/05</td></tr>
    </table></body></html>"""
    result = parse_aastocks_upcoming_html(html, today=_TODAY)
    assert len(result.items) == 1
    assert result.items[0].code == "01609.HK"


# ─── B. HTTP fetch 层 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_5xx_returns_empty() -> None:
    """5xx → AAStocksIPOFetchResult.empty(), 不抛 (与 eastmoney 一致)."""
    async with httpx.AsyncClient() as client:
        with respx.mock(base_url="http://www.aastocks.com") as mock:
            mock.get(
                "/sc/stocks/market/ipo/upcomingipo.aspx",
            ).mock(return_value=httpx.Response(503, text="Service Unavailable"))
            result = await fetch_aastocks_upcoming_with_client(client)
            assert result.items == []


@pytest.mark.asyncio
async def test_fetch_small_body_treated_as_empty() -> None:
    """body < 1024 byte (反爬常见的"go away"短响应) → 空结果."""
    async with httpx.AsyncClient() as client:
        with respx.mock(base_url="http://www.aastocks.com") as mock:
            mock.get(
                "/sc/stocks/market/ipo/upcomingipo.aspx",
            ).mock(return_value=httpx.Response(200, text="<html></html>"))
            result = await fetch_aastocks_upcoming_with_client(client)
            assert result.items == []


@pytest.mark.asyncio
async def test_fetch_happy_with_fixture(sample_html: str) -> None:
    """200 + happy fixture → 解析 OK 拿到 2 行."""
    # fixture 本身 < 1KB, 拼成 ~3KB 让通过 size check
    fat_body = "<!--padding " + "x" * 1500 + "-->\n" + sample_html
    async with httpx.AsyncClient() as client:
        with respx.mock(base_url="http://www.aastocks.com") as mock:
            mock.get(
                "/sc/stocks/market/ipo/upcomingipo.aspx",
            ).mock(return_value=httpx.Response(200, text=fat_body))
            result = await fetch_aastocks_upcoming_with_client(
                client,
                today=_TODAY,
            )
            assert len(result.items) == 2
            assert {it.code for it in result.items} == {"01609.HK", "01187.HK"}


def test_url_constant_is_https_or_http_aastocks() -> None:
    """URL 常量必须落在 aastocks.com 域 (防误改)."""
    assert "aastocks.com" in AASTOCKS_UPCOMING_URL
    assert AASTOCKS_UPCOMING_URL.endswith("upcomingipo.aspx")
