"""BE-S3-002: 智通财经 RSS 数据源单元测试.

覆盖 (≥ 5 条; spec/10 §AC 要求 ≥ 5 条):

A. ``parse_rss_feed`` 纯函数 (feedparser):
    1. happy: 标准 RSS 2.0 → 拿到 ArticleRaw 列表 (含正确 published_at + market='HK')
    2. 空字符串 / 空 feed → 返回空, 不抛
    3. Atom 格式 RSS → 也能解析 (feedparser 兼容性)
    4. 单条 entry 字段缺失 (无 link / 无 title) → 跳过
    5. 无 pubDate → fallback 用 ``updated`` 字段; 都没有 → 用 now()
    6. ``is_full_text_available=False`` 默认 (智通 RSS 仅授权摘要)

B. ``fetch_zhitong_with_client`` HTTP layer:
    7. 200 + 正常 RSS XML → 返回非空
    8. HTTP 5xx → warning + 空, 不抛
    9. ``httpx.RequestError`` → warning + 空

每条都 mock httpx 不依赖网络 / DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from textwrap import dedent

import httpx
import pytest
import respx

from app.services.article_ingest.sources.zhitong_rss_client import (
    fetch_zhitong_with_client,
    parse_rss_feed,
)

_RSS_URL = "https://www.zhitongcaijing.com/rss/news.xml"


_RSS_TEMPLATE_2_0 = dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>智通财经</title>
        <link>https://www.zhitongcaijing.com</link>
        <description>港股财经资讯</description>
        {items}
      </channel>
    </rss>
    """
)


def _rss_item(title: str, link: str, pub_date: str, summary: str = "") -> str:
    return dedent(
        f"""\
        <item>
          <title>{title}</title>
          <link>{link}</link>
          <pubDate>{pub_date}</pubDate>
          <description>{summary}</description>
        </item>
        """
    )


# ─── A. parse_rss_feed ───────────────────────────────────────────────


def test_parse_happy_two_entries() -> None:
    """标准 RSS 2.0 → 2 条 ArticleRaw, market='HK', credibility=3, is_full_text_available=False."""
    items = (
        _rss_item(
            "天星医疗递交香港 IPO 申请",
            "https://www.zhitongcaijing.com/news/100001",
            "Mon, 26 Apr 2026 09:00:00 GMT",
            "公司公告: 已递交港交所主板上市申请...",
        )
        + _rss_item(
            "腾讯Q1财报: 营收同比增长 12%",
            "https://www.zhitongcaijing.com/news/100002",
            "Mon, 26 Apr 2026 12:00:00 GMT",
            "腾讯公布 Q1 财报...",
        )
    )
    xml = _RSS_TEMPLATE_2_0.format(items=items)
    out = parse_rss_feed(xml)

    assert len(out) == 2
    art1 = out[0]
    assert art1.title == "天星医疗递交香港 IPO 申请"
    assert art1.original_url == "https://www.zhitongcaijing.com/news/100001"
    assert art1.source_name == "智通财经"
    assert art1.market == "HK"
    assert art1.source_credibility == 3
    assert art1.is_full_text_available is False
    # 2026-04-26 09:00 GMT
    assert art1.published_at == datetime(2026, 4, 26, 9, 0, tzinfo=UTC)
    assert "已递交港交所" in (art1.summary or "")


def test_parse_empty_xml_returns_empty() -> None:
    """空字符串 / 仅有 channel 没 item → 返回空."""
    assert parse_rss_feed("") == []
    out = parse_rss_feed(_RSS_TEMPLATE_2_0.format(items=""))
    assert out == []


def test_parse_atom_format() -> None:
    """Atom 格式 feed → feedparser 也能解析."""
    atom_xml = dedent(
        """\
        <?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>智通财经 Atom</title>
          <link href="https://www.zhitongcaijing.com"/>
          <updated>2026-04-26T09:00:00Z</updated>
          <entry>
            <title>Atom 文章</title>
            <link href="https://www.zhitongcaijing.com/atom/1"/>
            <updated>2026-04-26T09:00:00Z</updated>
            <summary>Atom 摘要</summary>
          </entry>
        </feed>
        """
    )
    out = parse_rss_feed(atom_xml)
    assert len(out) == 1
    assert out[0].title == "Atom 文章"
    # Atom 的 ``updated`` 字段会被 feedparser 同时填到 ``updated_parsed``
    # 与 ``published_parsed`` (无 pubDate 时 fallback)
    assert out[0].published_at == datetime(2026, 4, 26, 9, 0, tzinfo=UTC)


def test_parse_skips_entry_with_missing_fields() -> None:
    """单 entry 字段缺失 (无 title / 无 link) → 跳过, 不影响其它 entry."""
    items = (
        # 无 link
        dedent(
            """\
            <item>
              <title>无链接的文章</title>
              <pubDate>Mon, 26 Apr 2026 09:00:00 GMT</pubDate>
            </item>
            """
        )
        # 无 title
        + dedent(
            """\
            <item>
              <link>https://example.com/no_title</link>
              <pubDate>Mon, 26 Apr 2026 09:00:00 GMT</pubDate>
            </item>
            """
        )
        # 完整
        + _rss_item(
            "正常文章",
            "https://www.zhitongcaijing.com/news/normal",
            "Mon, 26 Apr 2026 09:00:00 GMT",
        )
    )
    xml = _RSS_TEMPLATE_2_0.format(items=items)
    out = parse_rss_feed(xml)
    assert len(out) == 1
    assert out[0].title == "正常文章"


def test_parse_no_pubdate_fallback_to_now() -> None:
    """entry 无 pubDate / updated → fallback 用 ``datetime.now(UTC)`` 兜底, 不丢条."""
    items = dedent(
        """\
        <item>
          <title>没时间</title>
          <link>https://example.com/no_time</link>
          <description>没 pubDate 的文章</description>
        </item>
        """
    )
    xml = _RSS_TEMPLATE_2_0.format(items=items)
    out = parse_rss_feed(xml)
    assert len(out) == 1
    # published_at 必然存在 (NOT NULL 兜底)
    assert out[0].published_at is not None
    assert out[0].published_at.tzinfo is not None


# ─── B. fetch_zhitong_with_client (HTTP layer) ──────────────────────────


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_fetch_with_client_happy(respx_mock: respx.Router) -> None:
    """200 + 正常 RSS XML → 返回非空."""
    items = _rss_item(
        "测试文章",
        "https://www.zhitongcaijing.com/news/test1",
        "Mon, 26 Apr 2026 09:00:00 GMT",
        "测试摘要",
    )
    xml = _RSS_TEMPLATE_2_0.format(items=items)
    respx_mock.get(_RSS_URL).mock(
        return_value=httpx.Response(
            200, text=xml, headers={"content-type": "application/rss+xml"}
        )
    )

    async with httpx.AsyncClient() as client:
        out = await fetch_zhitong_with_client(client, rss_url=_RSS_URL)

    assert len(out) == 1
    assert out[0].title == "测试文章"


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_fetch_with_client_5xx_returns_empty(respx_mock: respx.Router) -> None:
    """5xx → 返回空, 不抛."""
    respx_mock.get(_RSS_URL).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        out = await fetch_zhitong_with_client(client, rss_url=_RSS_URL)
    assert out == []


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_fetch_with_client_network_error_returns_empty(
    respx_mock: respx.Router,
) -> None:
    """``httpx.ConnectError`` → 空."""
    respx_mock.get(_RSS_URL).mock(side_effect=httpx.ConnectError("DNS down"))
    async with httpx.AsyncClient() as client:
        out = await fetch_zhitong_with_client(client, rss_url=_RSS_URL)
    assert out == []
