"""BUG-S8-003: ``longbridge_community_client`` 单元测试.

覆盖:

A. ``parse_longbridge_community_json`` 纯函数:
    1. happy 3 帖 (data.list schema) → 3 条 ArticleRaw
    2. unix 秒/毫秒兼容
    3. 缺 title 但有 content → content 首句作 title fallback
    4. 缺 content 也缺 title → skip
    5. 备选 schema (data.posts / payload.posts)
    6. user 嵌套对象 ({name: "..."}) → 取 name
    7. URL 去重

B. ``fetch_longbridge_community_with_client`` HTTP layer:
    8. 200 多 symbol → 解析 OK + 跨 symbol 去重
    9. 401 → 中止整批 (后续 symbol 不请求)
    10. 5xx 一个 symbol → 该 symbol skip + 其他继续

C. ``LongbridgeCommunityClient`` 行为:
    11. token=空 → is_enabled=False, fetch 立即返 [] (与新闻 API 共享 token)
    12. token 配 + 空 symbols → fetch 返 []
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import Settings
from app.services.article_ingest.sources.longbridge_api_client import (
    DEFAULT_LONGBRIDGE_BASE_URL,
)
from app.services.article_ingest.sources.longbridge_community_client import (
    LONGBRIDGE_COMMUNITY_PATH_DEFAULT,
    LONGBRIDGE_COMMUNITY_SOURCE_PREFIX,
    LongbridgeCommunityClient,
    fetch_longbridge_community_with_client,
    parse_longbridge_community_json,
)


def _make_settings(**overrides) -> Settings:
    base = {
        "longbridge_api_token": "",
        "longbridge_api_base_url": DEFAULT_LONGBRIDGE_BASE_URL,
        "longbridge_community_path": LONGBRIDGE_COMMUNITY_PATH_DEFAULT,
        "longbridge_api_max_queries": 20,
        "longbridge_api_inter_query_delay_seconds": 0.0,
        "article_ingest_request_timeout_seconds": 5.0,
        "article_ingest_request_concurrency": 2,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ─── A. parse_longbridge_community_json 纯函数 ────────────────────


def test_parse_happy_3_posts() -> None:
    payload = {
        "data": {
            "list": [
                {
                    "title": "腾讯今日值得关注",
                    "content": "腾讯Q1 财报亮眼...",
                    "link": "https://longbridge.com/posts/p1",
                    "author": "Cathie Wood",
                    "published_at": 1774022099,
                },
                {
                    "title": "",
                    "content": "新股可孚医疗,招股最后一天,我打算继续打 5 手.",
                    "link": "https://longbridge.com/posts/p2",
                    "user_name": "港股老炮",
                    "published_at": 1774025099,
                },
                {
                    "title": "天星医疗看法",
                    "content": "短期不建议",
                    "link": "https://longbridge.com/posts/p3",
                    "nickname": "新股小白",
                    "published_at": 1774028099,
                },
            ]
        }
    }
    out = parse_longbridge_community_json(payload, symbol="00700.HK")
    assert len(out) == 3
    assert out[0].source_name == f"{LONGBRIDGE_COMMUNITY_SOURCE_PREFIX}Cathie Wood"
    # 第二条标题空, 用 content 截首段
    assert out[1].title.startswith("新股可孚医疗")
    # market = "HK" (长桥主战场)
    assert all(a.market == "HK" for a in out)
    # source_credibility = 2 (社区 < 持牌媒体)
    assert all(a.source_credibility == 2 for a in out)
    assert all(a.is_full_text_available for a in out)


def test_parse_timestamp_seconds_and_millis() -> None:
    payload = {
        "data": {
            "list": [
                {
                    "title": "ms ts",
                    "content": "x",
                    "link": "https://x.com/a",
                    "author": "u",
                    "publishTime": 1774022099_000,
                },
                {
                    "title": "s ts",
                    "content": "x",
                    "link": "https://x.com/b",
                    "author": "u",
                    "publish_time": 1774022099,
                },
            ]
        }
    }
    out = parse_longbridge_community_json(payload)
    assert len(out) == 2
    assert out[0].published_at == out[1].published_at


def test_parse_no_title_no_content_skips() -> None:
    payload = {
        "data": {
            "list": [
                {"title": "", "content": "", "link": "https://x.com/a", "author": "u", "published_at": 1774022099},
                {"title": "ok", "content": "", "link": "https://x.com/b", "author": "u", "published_at": 1774022099},
            ]
        }
    }
    out = parse_longbridge_community_json(payload)
    assert len(out) == 1
    assert out[0].title == "ok"


def test_parse_alt_schema_posts_field() -> None:
    payload = {
        "data": {
            "posts": [
                {
                    "title": "alt",
                    "content": "x",
                    "url": "https://x.com/a",
                    "author": "u",
                    "created_at": 1774022099,
                }
            ]
        }
    }
    out = parse_longbridge_community_json(payload)
    assert len(out) == 1


def test_parse_user_nested_object() -> None:
    payload = {
        "data": {
            "list": [
                {
                    "title": "post by nested user",
                    "content": "x",
                    "link": "https://x.com/a",
                    "user": {"name": "Cathie", "id": 42},
                    "published_at": 1774022099,
                }
            ]
        }
    }
    out = parse_longbridge_community_json(payload)
    assert len(out) == 1
    assert out[0].source_name == f"{LONGBRIDGE_COMMUNITY_SOURCE_PREFIX}Cathie"


def test_parse_dedup_same_url() -> None:
    payload = {
        "data": {
            "list": [
                {
                    "title": "first",
                    "content": "x",
                    "link": "https://x.com/a",
                    "author": "u",
                    "published_at": 1774022099,
                },
                {
                    "title": "dup",
                    "content": "x",
                    "link": "https://x.com/a",
                    "author": "u2",
                    "published_at": 1774025099,
                },
            ]
        }
    }
    out = parse_longbridge_community_json(payload)
    assert len(out) == 1


# ─── B. fetch_longbridge_community_with_client HTTP layer ──────


@pytest.mark.asyncio
@respx.mock
async def test_fetch_happy_2_symbols_dedup() -> None:
    url = DEFAULT_LONGBRIDGE_BASE_URL + LONGBRIDGE_COMMUNITY_PATH_DEFAULT
    respx.get(url, params={"symbol": "00700.HK"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "list": [
                        {
                            "title": "post-A",
                            "content": "x",
                            "link": "https://lb.com/A",
                            "author": "u1",
                            "published_at": 1774022099,
                        },
                        {
                            "title": "shared",
                            "content": "x",
                            "link": "https://lb.com/shared",
                            "author": "u2",
                            "published_at": 1774022099,
                        },
                    ]
                }
            },
        )
    )
    respx.get(url, params={"symbol": "01187.HK"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "list": [
                        {
                            "title": "post-B",
                            "content": "x",
                            "link": "https://lb.com/B",
                            "author": "u3",
                            "published_at": 1774022099,
                        },
                        {
                            "title": "shared dup",
                            "content": "x",
                            "link": "https://lb.com/shared",
                            "author": "u4",
                            "published_at": 1774025099,
                        },
                    ]
                }
            },
        )
    )
    async with httpx.AsyncClient() as client:
        out = await fetch_longbridge_community_with_client(
            client,
            symbols=["00700.HK", "01187.HK"],
            inter_query_delay_seconds=0.0,
        )
    assert len(out) == 3
    titles = {a.title for a in out}
    assert "post-A" in titles
    assert "post-B" in titles


@pytest.mark.asyncio
@respx.mock
async def test_fetch_unauthorized_aborts_batch() -> None:
    url = DEFAULT_LONGBRIDGE_BASE_URL + LONGBRIDGE_COMMUNITY_PATH_DEFAULT
    route_a = respx.get(url, params={"symbol": "00700.HK"}).mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    route_b = respx.get(url, params={"symbol": "01187.HK"}).mock(
        return_value=httpx.Response(200, json={"data": {"list": []}})
    )
    async with httpx.AsyncClient() as client:
        out = await fetch_longbridge_community_with_client(
            client,
            symbols=["00700.HK", "01187.HK"],
            inter_query_delay_seconds=0.0,
        )
    assert out == []
    assert route_a.called
    assert not route_b.called


@pytest.mark.asyncio
@respx.mock
async def test_fetch_5xx_one_symbol_skips() -> None:
    url = DEFAULT_LONGBRIDGE_BASE_URL + LONGBRIDGE_COMMUNITY_PATH_DEFAULT
    respx.get(url, params={"symbol": "00700.HK"}).mock(
        return_value=httpx.Response(500)
    )
    respx.get(url, params={"symbol": "01187.HK"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "list": [
                        {
                            "title": "ok",
                            "content": "x",
                            "link": "https://lb.com/a",
                            "author": "u",
                            "published_at": 1774022099,
                        }
                    ]
                }
            },
        )
    )
    async with httpx.AsyncClient() as client:
        out = await fetch_longbridge_community_with_client(
            client,
            symbols=["00700.HK", "01187.HK"],
            inter_query_delay_seconds=0.0,
        )
    assert len(out) == 1
    assert out[0].title == "ok"


# ─── C. LongbridgeCommunityClient 行为 ──────────────────────────


@pytest.mark.asyncio
async def test_client_disabled_when_token_empty() -> None:
    settings = _make_settings(longbridge_api_token="")
    c = LongbridgeCommunityClient(settings=settings, symbols=["00700.HK"])
    assert c.is_enabled is False
    assert await c.fetch() == []


@pytest.mark.asyncio
async def test_client_enabled_with_token_empty_symbols() -> None:
    settings = _make_settings(longbridge_api_token="test-token")
    c = LongbridgeCommunityClient(settings=settings, symbols=[])
    assert c.is_enabled is True
    assert await c.fetch() == []
