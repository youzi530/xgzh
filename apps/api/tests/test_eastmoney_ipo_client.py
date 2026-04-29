"""BUG-S6.6-004: ``eastmoney_ipo_client`` 单元测试.

覆盖 (10 条):

A. ``parse_eastmoney_ipo_html`` 纯函数:
    1. happy 5 行 fixture → 拿到 5 条 IPOItem, 字段全对
    2. 解析 ``"24.86-24.86"`` 区间招股价 → 取上限 (Decimal)
    3. 解析 ``"77.7"`` 单值招股价 → 直接 Decimal
    4. 解析 ``"209.88"`` → 直接 Decimal (不带后缀)
    5. 解析 ``"-"`` 占位 → None
    6. 募集资金 ``"45.49亿"`` → 4.549e9 Decimal HKD
    7. 募集资金 ``"-"`` → None
    8. status 推断: 上市日期已过 today → "listed"
    9. status 推断: 招股期已开但未上市 → "subscribing"
    10. ``code`` 4 位补 0 (``"68"`` → ``"00068.HK"``)

B. ``fetch_eastmoney_ipo_list_with_client`` HTTP:
    11. HTTP 5xx → 空结果 (不抛, 与 hkex_client / akshare 一致)
    12. body 太小 (< 100 char) → 空结果 (反爬退化检测)

不依赖 PG / Redis.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters.eastmoney_ipo_client import (
    EASTMONEY_IPO_URL,
    fetch_eastmoney_ipo_list_with_client,
    parse_eastmoney_ipo_html,
)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "eastmoney_ipolist_sample.html"

# 用 fixture 截取时的"今天"参考: 商米科技 listing_date=2026-04-29
# 用 2026-04-25 作 reference, 让 5 条都是 upcoming/listed mix
_TODAY = date(2026, 4, 25)


@pytest.fixture
def sample_html() -> str:
    return _FIXTURE_PATH.read_text(encoding="utf-8")


# ─── A. parser 纯函数 ──────────────────────────────────────────


def test_parse_total_shares_collected(sample_html: str) -> None:
    """BUG-S6.7-002: total_shares 解析进 ``total_shares_by_code`` 旁路 dict.

    fixture 第 1 行: 商米科技-W 06810.HK 招股股数 ``"4262.68万"`` → 42626800
    """
    result = parse_eastmoney_ipo_html(sample_html, today=_TODAY)
    assert "06810.HK" in result.total_shares_by_code
    shares = result.total_shares_by_code["06810.HK"]
    # 4262.68 * 10000 = 42_626_800
    assert shares == Decimal("42626800")


def test_parse_happy_5_rows(sample_html: str) -> None:
    """fixture 5 行正常 HTML → 5 条 IPOItem, code/name/字段全对."""
    result = parse_eastmoney_ipo_html(sample_html, today=_TODAY)
    assert len(result.items) == 5

    codes = [it.code for it in result.items]
    assert codes == [
        "06810.HK",
        "01879.HK",
        "02493.HK",
        "03296.HK",
        "02476.HK",
    ]
    names = [it.name for it in result.items]
    assert names == [
        "商米科技-W",
        "曦智科技-P",
        "迈威生物-B",
        "华勤技术",
        "胜宏科技",
    ]
    # 都是 HK + HKD + eastmoney-ipolist source
    for it in result.items:
        assert it.market == "HK"
        assert it.issue_currency == "HKD"
        assert it.data_source == "eastmoney-ipolist"


def test_parse_issue_price_range_takes_upper(sample_html: str) -> None:
    """招股价区间 24.86-24.86 → 24.86; 166.60-183.20 → 183.20 (取上限)."""
    result = parse_eastmoney_ipo_html(sample_html, today=_TODAY)
    by_code = {it.code: it for it in result.items}
    assert by_code["06810.HK"].issue_price == Decimal("24.86")
    assert by_code["01879.HK"].issue_price == Decimal("183.20")


def test_parse_issue_price_single_value(sample_html: str) -> None:
    """招股价单值 77.7 / 209.88 → 直接 Decimal."""
    result = parse_eastmoney_ipo_html(sample_html, today=_TODAY)
    by_code = {it.code: it for it in result.items}
    assert by_code["03296.HK"].issue_price == Decimal("77.7")
    assert by_code["02476.HK"].issue_price == Decimal("209.88")


def test_parse_raised_amount_yi(sample_html: str) -> None:
    """募集资金 45.49亿 = 4.549e9 HKD; 201.17亿 = 2.0117e10 HKD."""
    result = parse_eastmoney_ipo_html(sample_html, today=_TODAY)
    by_code = {it.code: it for it in result.items}
    assert by_code["03296.HK"].raised_amount == Decimal("45.49") * Decimal("100000000")
    assert by_code["02476.HK"].raised_amount == Decimal("201.17") * Decimal("100000000")


def test_parse_subscribe_and_listing_dates(sample_html: str) -> None:
    """招股日期 2026-04-21, 上市日期 2026-04-29 → 字段对应正确."""
    result = parse_eastmoney_ipo_html(sample_html, today=_TODAY)
    sm = next(it for it in result.items if it.code == "06810.HK")
    assert sm.subscribe_start is not None
    assert sm.subscribe_start.date() == date(2026, 4, 21)
    assert sm.listing_date == date(2026, 4, 29)


def test_parse_status_inference_listed(sample_html: str) -> None:
    """today=2026-05-01: 全 5 行的上市日期都已过 → 全 listed."""
    result = parse_eastmoney_ipo_html(sample_html, today=date(2026, 5, 1))
    for it in result.items:
        assert it.status == "listed"


def test_parse_status_inference_subscribing(sample_html: str) -> None:
    """today=2026-04-22: 商米科技 (sub_start=04-21, listing=04-29) → subscribing."""
    result = parse_eastmoney_ipo_html(sample_html, today=date(2026, 4, 22))
    sm = next(it for it in result.items if it.code == "06810.HK")
    assert sm.status == "subscribing"


def test_parse_status_inference_upcoming(sample_html: str) -> None:
    """today=2026-04-10: 商米科技 (sub_start=04-21) → upcoming."""
    result = parse_eastmoney_ipo_html(sample_html, today=date(2026, 4, 10))
    sm = next(it for it in result.items if it.code == "06810.HK")
    assert sm.status == "upcoming"


def test_parse_empty_table_returns_empty() -> None:
    """没有 tbody 行 → 空结果, 不抛."""
    html = """
    <html><body>
      <table class="table table_striped center">
        <thead><tr><td>序号</td><td>股票代码</td><td>股票名称</td><td>招股价</td>
        <td>招股数(股)</td><td>募集资金(港元)</td><td>招股日期</td><td>上市日期</td></tr></thead>
        <tbody></tbody>
      </table>
    </body></html>
    """
    result = parse_eastmoney_ipo_html(html)
    assert result.items == []


def test_parse_no_table_returns_empty() -> None:
    """完全不是表格的 HTML → 空 + warning, 不抛."""
    html = "<html><body><h1>反爬</h1><p>nothing here</p></body></html>"
    result = parse_eastmoney_ipo_html(html)
    assert result.items == []


def test_parse_4digit_code_zfill() -> None:
    """4 位代码 ``68`` → ``00068.HK`` (zfill 5 位与项目其他 client 一致)."""
    html = """
    <html><body>
      <table class="table table_striped center">
        <thead><tr><td>序号</td><td>股票代码</td><td>股票名称</td><td>招股价</td>
        <td>招股数(股)</td><td>募集资金(港元)</td><td>招股日期</td><td>上市日期</td></tr></thead>
        <tbody>
          <tr>
            <td><span>1</span></td>
            <td><a>0068</a></td>
            <td><a>群核科技</a></td>
            <td><span>7.62</span></td>
            <td><span>16061.90万</span></td>
            <td><span>12.24亿</span></td>
            <td><span>2026-04-09</span></td>
            <td><span>2026-04-17</span></td>
          </tr>
        </tbody>
      </table>
    </body></html>
    """
    result = parse_eastmoney_ipo_html(html, today=_TODAY)
    assert len(result.items) == 1
    assert result.items[0].code == "00068.HK"


# ─── B. HTTP wrapper ──────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_fetch_5xx_returns_empty(sample_html: str) -> None:
    """500 → 空结果, 不抛."""
    respx.get(EASTMONEY_IPO_URL).mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        result = await fetch_eastmoney_ipo_list_with_client(client, today=_TODAY)
    assert result.items == []


@respx.mock
@pytest.mark.asyncio
async def test_fetch_tiny_body_returns_empty() -> None:
    """body < 100 字 → 反爬退化空响应保护; 返空."""
    respx.get(EASTMONEY_IPO_URL).mock(return_value=httpx.Response(200, text="<html/>"))
    async with httpx.AsyncClient() as client:
        result = await fetch_eastmoney_ipo_list_with_client(client, today=_TODAY)
    assert result.items == []


@respx.mock
@pytest.mark.asyncio
async def test_fetch_happy(sample_html: str) -> None:
    """200 + 正常 HTML → 拿到 5 条 IPOItem."""
    respx.get(EASTMONEY_IPO_URL).mock(
        return_value=httpx.Response(200, text=sample_html)
    )
    async with httpx.AsyncClient() as client:
        result = await fetch_eastmoney_ipo_list_with_client(client, today=_TODAY)
    assert len(result.items) == 5
    assert result.items[0].code == "06810.HK"
