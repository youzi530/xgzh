"""BUG-S7.3-001: ``longbridge_api_client`` 单元测试.

覆盖:

A. ``parse_longbridge_news_json`` 纯函数:
    1. happy 3 条 (data.list schema) → 3 条 ArticleRaw, 字段全部正确
    2. unix 秒 + 毫秒两种 timestamp → datetime UTC 正确
    3. 字段缺失 (title / link / published_at) → skip 单条
    4. 备选 schema (data.news / payload.list) → 兼容
    5. 空 payload / 非 dict → 空结果
    6. ``source_name`` 加 ``"长桥·"`` 前缀; 缺 source 字段 fallback "长桥·长桥"
    7. URL 重复 → 去重

B. ``fetch_longbridge_with_client`` HTTP layer:
    8. 200 + happy JSON → 解析 OK, 多 symbol 去重
    9. 401/403 → 中止整批 (不再请求后续 symbol)
    10. 5xx 一个 symbol → 该 symbol skip, 其它继续

C. ``LongbridgeApiClient`` 行为:
    11. token 未配置 → ``is_enabled=False``, ``fetch()`` 立即返 [] 不发 HTTP
    12. token 配置 + 空 symbols → fetch 返 []
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.core.config import Settings
from app.services.article_ingest.sources.longbridge_api_client import (
    DEFAULT_LONGBRIDGE_BASE_URL,
    LONGBRIDGE_NEWS_PATH,
    LONGBRIDGE_SOURCE_PREFIX,
    LongbridgeApiClient,
    fetch_longbridge_with_client,
    parse_longbridge_news_json,
)


def _make_settings(**overrides) -> Settings:
    """构造 Settings 实例, 默认 token=空 (不启用)."""
    base = {
        "longbridge_api_token": "",
        "longbridge_api_base_url": DEFAULT_LONGBRIDGE_BASE_URL,
        "longbridge_api_news_path": LONGBRIDGE_NEWS_PATH,
        "longbridge_api_max_queries": 20,
        "longbridge_api_inter_query_delay_seconds": 0.0,
        "article_ingest_request_timeout_seconds": 5.0,
        "article_ingest_request_concurrency": 2,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ─── A. parse_longbridge_news_json 纯函数 ────────────────────────────────


def test_parse_happy_3_items() -> None:
    payload = {
        "code": 0,
        "data": {
            "list": [
                {
                    "title": "可孚医疗 招股期 7 天",
                    "summary": "招股价 ...",
                    "link": "https://longbridge.com/news/n1",
                    "source": "证券日报",
                    "published_at": 1774022099,
                },
                {
                    "title": "天星医疗",
                    "link": "https://longbridge.com/news/n2",
                    "source": "新华财经",
                    "published_at": 1774025099,
                },
                {
                    "title": "曹操出行",
                    "link": "https://longbridge.com/news/n3",
                    "source": "智通财经",
                    "published_at": 1774028099,
                },
            ]
        },
    }
    out = parse_longbridge_news_json(payload, symbol="01187.HK")
    assert len(out) == 3
    a = out[0]
    assert a.title == "可孚医疗 招股期 7 天"
    assert a.source_name.startswith(LONGBRIDGE_SOURCE_PREFIX)
    assert a.source_name == "长桥·证券日报"
    assert a.original_url == "https://longbridge.com/news/n1"
    assert a.published_at == datetime.fromtimestamp(1774022099, tz=UTC)
    assert a.market == "HK"
    assert a.source_credibility == 3
    assert a.is_full_text_available is True
    assert a.summary == "招股价 ..."


def test_parse_timestamp_seconds_and_millis() -> None:
    """长桥可能给秒级或毫秒级时间戳; 都兼容."""
    payload = {
        "data": {
            "list": [
                {
                    "title": "毫秒级 ts",
                    "link": "https://x.com/a",
                    "source": "x",
                    "published_at": 1774022099_000,  # 毫秒
                },
                {
                    "title": "秒级 ts",
                    "link": "https://x.com/b",
                    "source": "x",
                    "published_at": 1774022099,  # 秒
                },
            ]
        }
    }
    out = parse_longbridge_news_json(payload)
    assert len(out) == 2
    assert out[0].published_at == out[1].published_at


def test_parse_skip_missing_required_fields() -> None:
    """缺 title / link / published_at → skip 单条."""
    payload = {
        "data": {
            "list": [
                {"title": "", "link": "https://x.com/a", "published_at": 1774022099},
                {"title": "ok", "link": "", "published_at": 1774022099},
                {"title": "ok", "link": "https://x.com/b", "published_at": None},
                {
                    "title": "ok",
                    "link": "https://x.com/c",
                    "published_at": 1774022099,
                },
            ]
        }
    }
    out = parse_longbridge_news_json(payload)
    assert len(out) == 1
    assert out[0].title == "ok"
    assert out[0].original_url == "https://x.com/c"


def test_parse_alt_schema_data_news_and_top_level_list() -> None:
    """备选 schema: data.news 与 payload.list 都接受."""
    p1 = {
        "data": {
            "news": [
                {
                    "title": "alt1",
                    "link": "https://x.com/a",
                    "source": "x",
                    "publish_time": 1774022099,
                }
            ]
        }
    }
    out1 = parse_longbridge_news_json(p1)
    assert len(out1) == 1

    p2 = {
        "list": [
            {
                "title": "alt2",
                "url": "https://x.com/b",
                "source_name": "y",
                "publishTime": 1774022099,
            }
        ]
    }
    out2 = parse_longbridge_news_json(p2)
    assert len(out2) == 1
    assert out2[0].source_name == "长桥·y"


def test_parse_empty_or_invalid_payload() -> None:
    assert parse_longbridge_news_json({}) == []
    assert parse_longbridge_news_json({"data": {}}) == []
    assert parse_longbridge_news_json({"data": {"list": []}}) == []
    assert parse_longbridge_news_json([]) == []  # type: ignore[arg-type]
    assert parse_longbridge_news_json("not a dict") == []  # type: ignore[arg-type]


def test_parse_missing_source_falls_back() -> None:
    """缺 source 字段 → fallback ``长桥·长桥``."""
    payload = {
        "data": {
            "list": [
                {
                    "title": "no source",
                    "link": "https://x.com/a",
                    "published_at": 1774022099,
                }
            ]
        }
    }
    out = parse_longbridge_news_json(payload)
    assert len(out) == 1
    assert out[0].source_name == "长桥·长桥"


def test_parse_dedup_same_url() -> None:
    payload = {
        "data": {
            "list": [
                {
                    "title": "first",
                    "link": "https://x.com/a",
                    "source": "x",
                    "published_at": 1774022099,
                },
                {
                    "title": "second (dup url)",
                    "link": "https://x.com/a",
                    "source": "x",
                    "published_at": 1774025099,
                },
            ]
        }
    }
    out = parse_longbridge_news_json(payload)
    assert len(out) == 1
    assert out[0].title == "first"


# ─── B. fetch_longbridge_with_client HTTP layer ───────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_fetch_happy_2_symbols_dedup() -> None:
    url = DEFAULT_LONGBRIDGE_BASE_URL + LONGBRIDGE_NEWS_PATH
    payload_a = {
        "data": {
            "list": [
                {
                    "title": "art-A",
                    "link": "https://x.com/A",
                    "source": "x",
                    "published_at": 1774022099,
                },
                {
                    "title": "shared",
                    "link": "https://x.com/shared",
                    "source": "x",
                    "published_at": 1774022099,
                },
            ]
        }
    }
    payload_b = {
        "data": {
            "list": [
                {
                    "title": "art-B",
                    "link": "https://x.com/B",
                    "source": "x",
                    "published_at": 1774022099,
                },
                {
                    "title": "shared dup",
                    "link": "https://x.com/shared",  # 跨 symbol 重复
                    "source": "x",
                    "published_at": 1774022099,
                },
            ]
        }
    }
    respx.get(url, params={"symbol": "00700.HK"}).mock(
        return_value=httpx.Response(200, json=payload_a)
    )
    respx.get(url, params={"symbol": "01187.HK"}).mock(
        return_value=httpx.Response(200, json=payload_b)
    )
    async with httpx.AsyncClient() as client:
        out = await fetch_longbridge_with_client(
            client,
            symbols=["00700.HK", "01187.HK"],
            inter_query_delay_seconds=0.0,
        )
    assert len(out) == 3
    titles = {a.title for a in out}
    assert "art-A" in titles
    assert "art-B" in titles
    # 共享 URL 仅保留一条
    urls = [a.original_url for a in out]
    assert urls.count("https://x.com/shared") == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_unauthorized_aborts_batch() -> None:
    """401 后立即中止, 后续 symbol 不再请求."""
    url = DEFAULT_LONGBRIDGE_BASE_URL + LONGBRIDGE_NEWS_PATH
    route_a = respx.get(url, params={"symbol": "00700.HK"}).mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    route_b = respx.get(url, params={"symbol": "01187.HK"}).mock(
        return_value=httpx.Response(200, json={"data": {"list": []}})
    )
    async with httpx.AsyncClient() as client:
        out = await fetch_longbridge_with_client(
            client,
            symbols=["00700.HK", "01187.HK"],
            inter_query_delay_seconds=0.0,
        )
    assert out == []
    assert route_a.called
    assert not route_b.called  # 中止后续


@pytest.mark.asyncio
@respx.mock
async def test_fetch_5xx_one_symbol_skips() -> None:
    """5xx 一个 symbol 跳过, 其它 symbol 正常."""
    url = DEFAULT_LONGBRIDGE_BASE_URL + LONGBRIDGE_NEWS_PATH
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
                            "link": "https://x.com/a",
                            "source": "x",
                            "published_at": 1774022099,
                        }
                    ]
                }
            },
        )
    )
    async with httpx.AsyncClient() as client:
        out = await fetch_longbridge_with_client(
            client,
            symbols=["00700.HK", "01187.HK"],
            inter_query_delay_seconds=0.0,
        )
    assert len(out) == 1
    assert out[0].title == "ok"


# ─── C. LongbridgeApiClient 行为 ────────────────────────────────────


@pytest.mark.asyncio
async def test_client_disabled_when_token_empty() -> None:
    settings = _make_settings(longbridge_api_token="")
    c = LongbridgeApiClient(settings=settings, symbols=["00700.HK"])
    assert c.is_enabled is False
    out = await c.fetch()
    assert out == []


@pytest.mark.asyncio
async def test_client_enabled_with_token_empty_symbols() -> None:
    settings = _make_settings(longbridge_api_token="test-token-xxx")
    c = LongbridgeApiClient(settings=settings, symbols=[])
    assert c.is_enabled is True
    out = await c.fetch()
    assert out == []
