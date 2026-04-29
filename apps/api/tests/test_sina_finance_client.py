"""BUG-S6.7-006: ``sina_finance_client`` 单元测试.

覆盖:

A. ``parse_sina_roll_response`` 纯函数:
    1. happy 3 条 → 拿到 3 条 ArticleRaw
    2. ``intime`` (unix sec) → ``published_at`` UTC 正确
    3. 缺 ``url`` 但有 ``wapurl`` → 用 wapurl 兜底
    4. 缺 title / 全部 url / intime → skip
    5. ``intime`` 异常值 (str/字符串数字也兼容/范围外) → skip
    6. 非 dict payload → 空结果

B. ``fetch_sina_with_client`` HTTP layer:
    7. 200 + 标准 JSON → 解析 OK
    8. HTTP 5xx → 空结果
    9. ``ValueError`` JSON parse 失败 → 空结果
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.services.article_ingest.sources.sina_finance_client import (
    SINA_ROLL_URL,
    fetch_sina_with_client,
    parse_sina_roll_response,
)


def _build_payload(items: list[dict]) -> dict:
    return {
        "result": {
            "status": {"code": 0, "msg": "ok"},
            "data": items,
        }
    }


def _build_item(
    *,
    title: str = "山西汾酒一季度报发布",
    url: str = "https://finance.sina.com.cn/stock/roll/2026-04-29/doc-x.shtml",
    wapurl: str = "https://finance.sina.cn/2026-04-29/detail-x.d.html",
    intime: int | str = 1777454025,
    media_name: str = "新浪证券",
    intro: str = "4 月 29 日, 山西汾酒...",
) -> dict:
    return {
        "title": title,
        "url": url,
        "wapurl": wapurl,
        "intime": intime,
        "media_name": media_name,
        "intro": intro,
    }


# ─── A. parser 纯函数 ──────────────────────────────────────────


def test_parse_happy_3_items() -> None:
    payload = _build_payload(
        [
            _build_item(title="新闻 A", url="https://x.com/a"),
            _build_item(title="新闻 B", url="https://x.com/b"),
            _build_item(title="新闻 C", url="https://x.com/c"),
        ]
    )
    arts = parse_sina_roll_response(payload)
    assert len(arts) == 3
    assert {a.title for a in arts} == {"新闻 A", "新闻 B", "新闻 C"}


def test_parse_intime_unix_to_utc() -> None:
    """``intime=1777454025`` → ``2026-04-29 11:13:45 UTC`` (CST 19:13:45 - 8h)."""
    payload = _build_payload([_build_item(intime=1777454025)])
    arts = parse_sina_roll_response(payload)
    assert len(arts) == 1
    assert arts[0].published_at == datetime.fromtimestamp(1777454025, tz=UTC)


def test_parse_falls_back_to_wapurl() -> None:
    payload = _build_payload(
        [_build_item(url="", wapurl="https://m.sina.cn/article/1")]
    )
    arts = parse_sina_roll_response(payload)
    assert len(arts) == 1
    assert arts[0].original_url == "https://m.sina.cn/article/1"


def test_parse_skips_missing_required_fields() -> None:
    """缺 title 或全部 url 或 intime → skip."""
    payload = _build_payload(
        [
            _build_item(title=""),  # 空 title
            _build_item(url="", wapurl=""),  # 全空 url
            _build_item(intime=0),  # intime 0 视为缺
            _build_item(),  # 完整 → 入选
        ]
    )
    arts = parse_sina_roll_response(payload)
    assert len(arts) == 1


def test_parse_intime_string_compat() -> None:
    """``intime`` 字符串 (新浪偶尔返字符串) → 也能解析."""
    payload = _build_payload([_build_item(intime="1777454025")])
    arts = parse_sina_roll_response(payload)
    assert len(arts) == 1


def test_parse_intime_out_of_range_skipped() -> None:
    """``intime`` 异常 (1900 / 2200 / 负值) → skip."""
    payload = _build_payload(
        [
            _build_item(intime=-1),
            _build_item(intime=99999999999, title="2200 年新闻"),
            _build_item(),
        ]
    )
    arts = parse_sina_roll_response(payload)
    assert len(arts) == 1


def test_parse_non_dict_payload_returns_empty() -> None:
    assert parse_sina_roll_response([]) == []  # type: ignore[arg-type]
    assert parse_sina_roll_response(None) == []  # type: ignore[arg-type]


# ─── B. HTTP fetch ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_happy() -> None:
    payload = _build_payload([_build_item(title="新浪滚动测试")])
    async with httpx.AsyncClient() as client:
        with respx.mock(base_url="https://feed.mix.sina.com.cn") as mock:
            mock.get("/api/roll/get").mock(
                return_value=httpx.Response(200, json=payload)
            )
            arts = await fetch_sina_with_client(client)
            assert len(arts) == 1
            assert arts[0].title == "新浪滚动测试"


@pytest.mark.asyncio
async def test_fetch_5xx_returns_empty() -> None:
    async with httpx.AsyncClient() as client:
        with respx.mock(base_url="https://feed.mix.sina.com.cn") as mock:
            mock.get("/api/roll/get").mock(return_value=httpx.Response(503))
            arts = await fetch_sina_with_client(client)
            assert arts == []


@pytest.mark.asyncio
async def test_fetch_invalid_json_returns_empty() -> None:
    """JSON parse 失败 (上游返 HTML 错误页) → 空."""
    async with httpx.AsyncClient() as client:
        with respx.mock(base_url="https://feed.mix.sina.com.cn") as mock:
            mock.get("/api/roll/get").mock(
                return_value=httpx.Response(200, text="<html>error</html>")
            )
            arts = await fetch_sina_with_client(client)
            assert arts == []


def test_url_constant_is_sina_feed() -> None:
    assert "feed.mix.sina.com.cn" in SINA_ROLL_URL
    assert "/api/roll/get" in SINA_ROLL_URL
