"""BUG-S6.9-001: ``sogou_wechat_client`` 单元测试.

覆盖:

A. ``parse_sogou_html`` 纯函数:
    1. happy 3 条 → 3 条 ArticleRaw, 字段 (title / source_name 含前缀 /
       published_at UTC / summary / original_url 拼绝对) 全部正确
    2. ``<em>`` 高亮 + ``<!--red_beg/end-->`` 注释剥离
    3. ``timeConvert('1774022099')`` → datetime UTC 正确; 范围外 ts → skip
    4. 缺 title / 缺 href / 缺公众号名 / 缺时间戳 → skip 单条
    5. 反爬 HTML (``antispider`` / ``请输入验证码``) → 整批返 [] + log warn
    6. 空 / None HTML → 空结果

B. ``fetch_sogou_with_client`` HTTP layer:
    7. 200 + happy HTML → 解析 OK, 多 query 去重
    8. HTTP 5xx 一个 query → 该 query skip, 其它 query 不受影响
    9. 反爬触发 → 返 []

C. ``SogouWechatClient.__init__``:
    10. 空 queries → fetch 立即返 []
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.services.article_ingest.sources.sogou_wechat_client import (
    SOGOU_WECHAT_URL,
    WECHAT_SOURCE_PREFIX,
    SogouWechatClient,
    fetch_sogou_with_client,
    parse_sogou_html,
)


def _build_item(
    *,
    box_id: int = 0,
    title: str = "可孚医疗:'智造'健康管家",
    href: str = "/link?url=dn9a_xyz&type=2&query=test&token=abc",
    nick: str = "证券日报之声",
    ts: int = 1774022099,
    summary: str = "可孚医疗货品周转与客户响应效率大幅提升...",
) -> str:
    """构造单条 li 模拟搜狗 HTML."""
    return f'''
    <li id="sogou_vr_11002601_box_{box_id}">
      <div class="img-box"><a href="{href}"><img src="//x.png"/></a></div>
      <div class="txt-box">
        <h3>
          <a href="{href}" id="sogou_vr_11002601_title_{box_id}" target="_blank">
            <em><!--red_beg-->可孚医疗<!--red_end--></em>{title.replace("可孚医疗", "")}
          </a>
        </h3>
        <p class="txt-info" id="sogou_vr_11002601_summary_{box_id}">
          <em><!--red_beg-->可孚医疗<!--red_end-->-->{summary.replace("可孚医疗", "")}
        </p>
        <div class="s-p">
          <span class="all-time-y2">{nick}</span>
          <span class="s2"><script>document.write(timeConvert('{ts}'))</script></span>
        </div>
      </div>
    </li>
    '''


def _build_html(items_html: list[str]) -> str:
    """组成完整搜狗页 (含 ul.news-list 容器)."""
    return f"""
    <html><body>
    <ul class="news-list">{''.join(items_html)}</ul>
    </body></html>
    """


# ─── A. parse_sogou_html 纯函数 ────────────────────────────────────────


def test_parse_happy_three_items() -> None:
    html = _build_html(
        [
            _build_item(box_id=0, nick="证券日报之声", ts=1774022099),
            _build_item(box_id=1, nick="珍兴资本", ts=1774108499),
            _build_item(box_id=2, nick="Kai的费曼学习", ts=1774194899),
        ]
    )
    out = parse_sogou_html(html, query="可孚医疗")
    assert len(out) == 3
    nicks = [a.source_name for a in out]
    assert nicks == [
        f"{WECHAT_SOURCE_PREFIX}证券日报之声",
        f"{WECHAT_SOURCE_PREFIX}珍兴资本",
        f"{WECHAT_SOURCE_PREFIX}Kai的费曼学习",
    ]
    # source_credibility = 2, market = BOTH, is_full_text_available = False
    for art in out:
        assert art.source_credibility == 2
        assert art.market == "BOTH"
        assert art.is_full_text_available is False
        assert art.original_url.startswith("https://weixin.sogou.com/link?url=")


def test_parse_strips_em_and_red_comments() -> None:
    html = _build_html([_build_item(box_id=0, nick="证券日报")])
    out = parse_sogou_html(html, query="可孚")
    assert len(out) == 1
    # title 不应该再有 <em> 或 <!--red_*--> 痕迹
    assert "<em>" not in out[0].title
    assert "red_beg" not in out[0].title
    assert "red_end" not in out[0].title
    # 但应包含 IPO 名 (从 em 标签里抽出来的)
    assert "可孚医疗" in out[0].title


def test_parse_timestamp_to_utc() -> None:
    # 1774022099 = 2026-03-20 15:54:59 UTC
    html = _build_html([_build_item(box_id=0, ts=1774022099)])
    out = parse_sogou_html(html, query="可孚")
    assert len(out) == 1
    assert out[0].published_at == datetime(2026, 3, 20, 15, 54, 59, tzinfo=UTC)


def test_parse_timestamp_out_of_range_skipped() -> None:
    # ts < 1980-01-01 (315532800) → skip
    html = _build_html([_build_item(box_id=0, ts=10000)])
    out = parse_sogou_html(html, query="可孚")
    assert out == []


def test_parse_skips_missing_fields() -> None:
    # 没 title (h3 a 完全空)
    html_no_title = """
    <html><body><ul class="news-list">
      <li id="sogou_vr_11002601_box_0">
        <div class="txt-box">
          <h3><a href="/link?url=x"></a></h3>
          <div class="s-p">
            <span class="all-time-y2">大V</span>
            <span class="s2"><script>document.write(timeConvert('1774022099'))</script></span>
          </div>
        </div>
      </li>
    </ul></body></html>
    """
    assert parse_sogou_html(html_no_title) == []

    # 没 href
    html_no_href = """
    <html><body><ul class="news-list">
      <li id="sogou_vr_11002601_box_0">
        <div class="txt-box">
          <h3><a>有标题但没 href</a></h3>
          <div class="s-p">
            <span class="all-time-y2">大V</span>
            <span class="s2"><script>document.write(timeConvert('1774022099'))</script></span>
          </div>
        </div>
      </li>
    </ul></body></html>
    """
    assert parse_sogou_html(html_no_href) == []

    # 没公众号名
    html_no_nick = """
    <html><body><ul class="news-list">
      <li id="sogou_vr_11002601_box_0">
        <div class="txt-box">
          <h3><a href="/link?url=x">title</a></h3>
          <div class="s-p">
            <span class="s2"><script>document.write(timeConvert('1774022099'))</script></span>
          </div>
        </div>
      </li>
    </ul></body></html>
    """
    assert parse_sogou_html(html_no_nick) == []

    # 没时间戳
    html_no_ts = """
    <html><body><ul class="news-list">
      <li id="sogou_vr_11002601_box_0">
        <div class="txt-box">
          <h3><a href="/link?url=x">title</a></h3>
          <div class="s-p">
            <span class="all-time-y2">大V</span>
          </div>
        </div>
      </li>
    </ul></body></html>
    """
    assert parse_sogou_html(html_no_ts) == []


def test_parse_antispider_html_returns_empty() -> None:
    html_antispider = "<html><body>请输入验证码</body></html>"
    assert parse_sogou_html(html_antispider) == []
    html_antispider2 = """<html><script>location.href='/antispider/?from=...'</script></html>"""
    assert parse_sogou_html(html_antispider2) == []


def test_parse_empty_or_none_html() -> None:
    assert parse_sogou_html("") == []
    assert parse_sogou_html("   ") == []


# ─── B. fetch_sogou_with_client HTTP layer ──────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_fetch_happy_multi_queries_dedup() -> None:
    """两个 query 各返 1 条不同 URL → 合并返 2 条."""
    respx.get(SOGOU_WECHAT_URL, params={"type": "2", "query": "可孚医疗"}).mock(
        return_value=httpx.Response(
            200,
            text=_build_html(
                [_build_item(box_id=0, href="/link?url=A", nick="大V1", ts=1774022099)]
            ),
        )
    )
    respx.get(SOGOU_WECHAT_URL, params={"type": "2", "query": "天星医疗"}).mock(
        return_value=httpx.Response(
            200,
            text=_build_html(
                [_build_item(box_id=0, href="/link?url=B", nick="大V2", ts=1774108499)]
            ),
        )
    )
    async with httpx.AsyncClient() as client:
        out = await fetch_sogou_with_client(
            client,
            queries=["可孚医疗", "天星医疗"],
            request_timeout=5,
            inter_query_delay_seconds=0,
        )
    assert len(out) == 2
    urls = {a.original_url for a in out}
    assert urls == {
        "https://weixin.sogou.com/link?url=A",
        "https://weixin.sogou.com/link?url=B",
    }


@pytest.mark.asyncio
@respx.mock
async def test_fetch_dedup_same_url_across_queries() -> None:
    """同一 URL 在两个 query 里都出现 → 去重保留首条."""
    same_html = _build_html([_build_item(box_id=0, href="/link?url=DUP", nick="大V")])
    respx.get(SOGOU_WECHAT_URL, params={"type": "2", "query": "可孚医疗"}).mock(
        return_value=httpx.Response(200, text=same_html)
    )
    respx.get(SOGOU_WECHAT_URL, params={"type": "2", "query": "天星医疗"}).mock(
        return_value=httpx.Response(200, text=same_html)
    )
    async with httpx.AsyncClient() as client:
        out = await fetch_sogou_with_client(
            client,
            queries=["可孚医疗", "天星医疗"],
            request_timeout=5,
            inter_query_delay_seconds=0,
        )
    assert len(out) == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_5xx_skips_that_query() -> None:
    """一个 query 5xx → skip + 继续其它 query."""
    respx.get(SOGOU_WECHAT_URL, params={"type": "2", "query": "可孚医疗"}).mock(
        return_value=httpx.Response(503, text="bad")
    )
    respx.get(SOGOU_WECHAT_URL, params={"type": "2", "query": "天星医疗"}).mock(
        return_value=httpx.Response(
            200,
            text=_build_html([_build_item(box_id=0, nick="大V", href="/link?url=OK")]),
        )
    )
    async with httpx.AsyncClient() as client:
        out = await fetch_sogou_with_client(
            client,
            queries=["可孚医疗", "天星医疗"],
            request_timeout=5,
            inter_query_delay_seconds=0,
        )
    assert len(out) == 1
    assert out[0].original_url == "https://weixin.sogou.com/link?url=OK"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_antispider_returns_empty() -> None:
    respx.get(SOGOU_WECHAT_URL, params={"type": "2", "query": "可孚医疗"}).mock(
        return_value=httpx.Response(200, text="<html>请输入验证码</html>")
    )
    async with httpx.AsyncClient() as client:
        out = await fetch_sogou_with_client(
            client,
            queries=["可孚医疗"],
            request_timeout=5,
            inter_query_delay_seconds=0,
        )
    assert out == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_inter_query_delay_throttles() -> None:
    """节流: 2 个 query 之间要 sleep — wall clock 对照."""
    import time

    respx.get(SOGOU_WECHAT_URL, params={"type": "2", "query": "Q1"}).mock(
        return_value=httpx.Response(
            200,
            text=_build_html([_build_item(box_id=0, href="/link?url=Q1", nick="V")]),
        )
    )
    respx.get(SOGOU_WECHAT_URL, params={"type": "2", "query": "Q2"}).mock(
        return_value=httpx.Response(
            200,
            text=_build_html([_build_item(box_id=0, href="/link?url=Q2", nick="V")]),
        )
    )
    async with httpx.AsyncClient() as client:
        t0 = time.monotonic()
        out = await fetch_sogou_with_client(
            client,
            queries=["Q1", "Q2"],
            request_timeout=5,
            inter_query_delay_seconds=0.3,
        )
        elapsed = time.monotonic() - t0
    # 2 个 query, 1 个间隔, 至少等 0.3s
    assert elapsed >= 0.3
    assert len(out) == 2


# ─── C. SogouWechatClient.fetch ──────────────────────────────────────


@pytest.mark.asyncio
async def test_client_empty_queries_returns_empty() -> None:
    client = SogouWechatClient(queries=[])
    out = await client.fetch()
    assert out == []
