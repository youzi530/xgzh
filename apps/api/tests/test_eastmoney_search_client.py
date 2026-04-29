"""BUG-S6.7-005: ``eastmoney_search_client`` 单元测试.

覆盖:

A. ``parse_eastmoney_search_response`` 纯函数:
    1. happy 3 条 → 拿到 3 条 ArticleRaw, 字段全对
    2. ``<em>关键词</em>`` 高亮 tag 被 ``_strip_em`` 清理
    3. ``mediaName`` → ``source_name`` (动态来源)
    4. 缺 ``title`` / ``url`` / ``date`` → skip 该条
    5. 非 dict payload (API 改版) → 空结果不抛
    6. ``date`` 解析为 CST → 转 UTC 正确
    7. ``cmsArticleWebOld`` 不是 list → 空结果

B. ``fetch_eastmoney_search_with_client`` HTTP layer:
    8. 200 + 多 queries → 合并 + 去重 (相同 url 不重复)
    9. HTTP 5xx → 空结果 (与 xueqiu 一致 fail-soft)
    10. JSONP 包装 ``({...});`` 正确解包
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.services.article_ingest.sources.eastmoney_search_client import (
    EASTMONEY_SEARCH_URL,
    fetch_eastmoney_search_with_client,
    parse_eastmoney_search_response,
)

_CST = timezone(timedelta(hours=8))


def _build_payload(items: list[dict]) -> dict:
    """构造 EM-search 响应模板."""
    return {
        "code": 0,
        "hitsTotal": len(items),
        "result": {"cmsArticleWebOld": items},
    }


def _build_item(
    *,
    title: str = "<em>可孚医疗</em>一季度净利1.07亿元",
    url: str = "http://finance.eastmoney.com/a/202604293724502367.html",
    date: str = "2026-04-29 16:43:57",
    media_name: str = "北京商报",
    content: str = "<em>可孚医疗</em>表示, 报告期内...",
) -> dict:
    return {
        "title": title,
        "url": url,
        "date": date,
        "mediaName": media_name,
        "content": content,
        "image": "",
        "code": "202604293724502367",
    }


# ─── A. parser 纯函数 ──────────────────────────────────────────


def test_parse_happy_3_items() -> None:
    payload = _build_payload(
        [
            _build_item(title="<em>可孚医疗</em>: 一季度净利 1.07 亿"),
            _build_item(
                title="<em>可孚医疗</em> 第一季度业绩",
                url="http://finance.eastmoney.com/a/2.html",
                media_name="证券时报网",
            ),
            _build_item(
                title="界面: <em>可孚医疗</em> 增长 17%",
                url="http://finance.eastmoney.com/a/3.html",
                media_name="界面新闻",
            ),
        ]
    )
    arts = parse_eastmoney_search_response(payload)
    assert len(arts) == 3
    # 媒体名动态 → 各自 source_name
    sources = [a.source_name for a in arts]
    assert "北京商报" in sources
    assert "证券时报网" in sources
    assert "界面新闻" in sources


def test_parse_strips_em_tag() -> None:
    """``<em>可孚医疗</em>一季度净利`` → ``可孚医疗一季度净利``."""
    payload = _build_payload([_build_item(title="<em>可孚医疗</em>一季度净利")])
    arts = parse_eastmoney_search_response(payload)
    assert len(arts) == 1
    assert arts[0].title == "可孚医疗一季度净利"
    assert "<em>" not in arts[0].title


def test_parse_uses_media_name_as_source() -> None:
    payload = _build_payload([_build_item(media_name="人民财讯")])
    arts = parse_eastmoney_search_response(payload)
    assert arts[0].source_name == "人民财讯"


def test_parse_missing_required_fields_skipped() -> None:
    """缺 title / url / date 任一 → skip."""
    payload = _build_payload(
        [
            _build_item(title=""),  # 空 title
            _build_item(url=""),  # 空 url
            _build_item(date=""),  # 空 date
            _build_item(),  # 完整 → 入选
        ]
    )
    arts = parse_eastmoney_search_response(payload)
    assert len(arts) == 1


def test_parse_non_dict_payload_returns_empty() -> None:
    """API 改版返 list / None → 空结果不抛."""
    assert parse_eastmoney_search_response([]) == []  # type: ignore[arg-type]
    assert parse_eastmoney_search_response("error") == []  # type: ignore[arg-type]


def test_parse_date_cst_to_utc() -> None:
    """``"2026-04-29 16:43:57"`` (CST) → UTC datetime (减 8h)."""
    payload = _build_payload([_build_item(date="2026-04-29 16:43:57")])
    arts = parse_eastmoney_search_response(payload)
    assert len(arts) == 1
    expected_cst = datetime(2026, 4, 29, 16, 43, 57, tzinfo=_CST)
    assert arts[0].published_at == expected_cst.astimezone(UTC)


def test_parse_articles_field_not_list_returns_empty() -> None:
    """``cmsArticleWebOld`` 不是 list (API 改版) → 空结果."""
    payload = {
        "code": 0,
        "result": {"cmsArticleWebOld": "not-a-list"},
    }
    assert parse_eastmoney_search_response(payload) == []


# ─── B. HTTP fetch 层 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_multi_queries_dedup() -> None:
    """两个 query 命中同一篇文章 (相同 URL) → 只入结果一次."""
    payload = _build_payload(
        [
            _build_item(url="http://x.com/a.html", title="A"),
            _build_item(url="http://x.com/b.html", title="B"),
        ]
    )
    async with httpx.AsyncClient() as client:
        with respx.mock(base_url="https://search-api-web.eastmoney.com") as mock:
            # JSONP 包装: ``({...});``
            import json as _j
            jsonp = f"({_j.dumps(payload, ensure_ascii=False)});"
            mock.get("/search/jsonp").mock(
                return_value=httpx.Response(200, text=jsonp)
            )
            arts = await fetch_eastmoney_search_with_client(
                client, queries=["可孚医疗", "天星医疗"]
            )
            # 4 条命中 (2 query × 2 item) 去重后 2 条
            urls = {a.original_url for a in arts}
            assert urls == {"http://x.com/a.html", "http://x.com/b.html"}


@pytest.mark.asyncio
async def test_fetch_5xx_skips_query() -> None:
    """单 query 5xx → 空结果, 不抛."""
    async with httpx.AsyncClient() as client:
        with respx.mock(base_url="https://search-api-web.eastmoney.com") as mock:
            mock.get("/search/jsonp").mock(return_value=httpx.Response(503))
            arts = await fetch_eastmoney_search_with_client(
                client, queries=["可孚医疗"]
            )
            assert arts == []


@pytest.mark.asyncio
async def test_fetch_jsonp_unwrap() -> None:
    """JSONP 包装 ``foo({...});`` 也能解包正确 (有些 cb 非空)."""
    payload = _build_payload([_build_item(title="<em>可孚</em>测试")])
    import json as _j
    jsonp = f"jQuery_cb12345({_j.dumps(payload, ensure_ascii=False)});"
    async with httpx.AsyncClient() as client:
        with respx.mock(base_url="https://search-api-web.eastmoney.com") as mock:
            mock.get("/search/jsonp").mock(
                return_value=httpx.Response(200, text=jsonp)
            )
            arts = await fetch_eastmoney_search_with_client(
                client, queries=["可孚医疗"]
            )
            assert len(arts) == 1
            assert arts[0].title == "可孚测试"


def test_url_constant_is_eastmoney_search_api() -> None:
    """URL 常量必须落在 search-api-web.eastmoney.com 域."""
    assert "search-api-web.eastmoney.com" in EASTMONEY_SEARCH_URL
    assert "/search/jsonp" in EASTMONEY_SEARCH_URL


# ─── C. _build_param: double-encode 回归 ────────────────────────


def test_build_param_returns_raw_json_not_urlencoded() -> None:
    """回归 BUG-S6.7-005: ``_build_param`` 返 **raw JSON**, 不能预先 ``quote``.

    httpx ``params=`` 会自己做 URL encode, 预先 encode 会让 ``%7B`` → ``%257B``
    这个 double escape 服务器返 400 "非法的 json 格式".
    """
    from app.services.article_ingest.sources.eastmoney_search_client import (
        _build_param,
    )

    p = _build_param("可孚医疗")
    # 必须是 raw JSON (含中文 / 大括号 / 引号)
    assert p.startswith("{")
    assert p.endswith("}")
    assert "可孚医疗" in p
    assert '"keyword":"可孚医疗"' in p
    assert '"pageSize":10' in p
    # 不可以是 URL-encoded
    assert "%7B" not in p
    assert "%22" not in p


@pytest.mark.asyncio
async def test_fetch_does_not_double_encode_param() -> None:
    """httpx 实际发请求时, ``param`` query 不可以是 ``%257B`` (double escape)."""
    payload = _build_payload([_build_item()])
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["raw_query"] = request.url.query.decode()
        import json as _j
        jsonp = f"({_j.dumps(payload, ensure_ascii=False)});"
        return httpx.Response(200, text=jsonp)

    async with httpx.AsyncClient() as client:
        with respx.mock(base_url="https://search-api-web.eastmoney.com") as mock:
            mock.get("/search/jsonp").mock(side_effect=_handler)
            await fetch_eastmoney_search_with_client(client, queries=["可孚医疗"])

    raw = captured["raw_query"]
    # %7B (single encoded `{`) 必须出现, %257B (double encoded) 必须不出现
    assert "%7B" in raw, f"expected single-encoded {{, got: {raw}"
    assert "%257B" not in raw, f"detected double-encoding: {raw}"
    assert "%2522" not in raw, f"detected double-encoded quotes: {raw}"
