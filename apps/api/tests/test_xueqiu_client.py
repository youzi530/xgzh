"""BE-S3-002: 雪球数据源单元测试.

覆盖 (≥ 6 条; spec/10 §AC 要求 ≥ 6 条):

A. ``parse_status_list_json`` 纯函数:
    1. happy: 标准雪球 status JSON → 拿到 ArticleRaw 列表 + 字段正确解析
    2. 空 ``list`` 字段 → 返回空, 不抛
    3. 非 dict payload → 返回空 (防御 API 改版返 list / null)
    4. 单条 entry 字段缺失 (无 title / 无 target / 无 created_at) → 跳过该行
    5. HTML tag 在 description 里 → 被 ``_strip_html`` 清理
    6. ``hot_score`` 公式: ``view + 3*reply + 5*like``
    7. created_at ms → datetime UTC 正确转换

B. ``fetch_xueqiu_with_client`` HTTP layer:
    8. 200 + 正常 JSON + 多 query → 全部入结果, ``original_url`` 去重
    9. HTTP 5xx → warning + 跳过该 query, 不影响其它 query
    10. ``httpx.RequestError`` → warning + 空, 不抛

每条都 mock httpx 不依赖网络 / DB; 不需要 ``@pytest.mark.db``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx

from app.services.article_ingest.sources.base import ArticleRaw
from app.services.article_ingest.sources.xueqiu_client import (
    XUEQIU_STATUS_SEARCH_PATH,
    fetch_xueqiu_with_client,
    parse_status_list_json,
)

_XUEQIU_BASE_URL = "https://xueqiu.com"


def _build_payload(items: list[dict]) -> dict:
    """构造雪球 status.json 响应模板."""
    return {"list": items, "next_max_id": 0, "count": len(items)}


def _build_item(
    *,
    title: str,
    target: str = "/zhuanlan/12345",
    created_at_ms: int = 1714031234000,
    description: str = "",
    view: int = 0,
    reply: int = 0,
    like: int = 0,
) -> dict:
    return {
        "id": hash(target) & 0xFFFFFFFF,
        "title": title,
        "target": target,
        "created_at": created_at_ms,
        "description": description,
        "view_count": view,
        "reply_count": reply,
        "like_count": like,
    }


# ─── A. parse_status_list_json ──────────────────────────────────────────


def test_parse_happy_two_items() -> None:
    """标准 happy: 2 条 status → 各 1 条 ArticleRaw, 字段一一对应."""
    payload = _build_payload(
        [
            _build_item(
                title="天星医疗 IPO 定价 21.6 元",
                target="/zhuanlan/100001",
                created_at_ms=1714031234000,
                description="<p>本次 IPO 募资 25 亿</p>",
                view=1000,
                reply=20,
                like=50,
            ),
            _build_item(
                title="某科技公司路演纪要",
                target="/p/200002",
                created_at_ms=1714117634000,
                description="路演要点",
            ),
        ]
    )
    out = parse_status_list_json(payload, base_url=_XUEQIU_BASE_URL)
    assert len(out) == 2
    assert isinstance(out[0], ArticleRaw)

    item1 = out[0]
    assert item1.title == "天星医疗 IPO 定价 21.6 元"
    assert item1.original_url == "https://xueqiu.com/zhuanlan/100001"
    assert item1.source_name == "雪球"
    # hot: 1000 + 3*20 + 5*50 = 1310
    assert item1.hot_score == Decimal(1310)
    # created_at_ms 1714031234000 → 2024-04-25 ish
    assert item1.published_at == datetime.fromtimestamp(1714031234, tz=UTC)
    # HTML 被 strip
    assert item1.summary == "本次 IPO 募资 25 亿"
    assert item1.related_ipos == []
    assert item1.is_full_text_available is True


def test_parse_empty_list() -> None:
    """``list`` 字段空 → 返回空, 不抛."""
    out = parse_status_list_json({"list": []}, base_url=_XUEQIU_BASE_URL)
    assert out == []


def test_parse_no_list_field() -> None:
    """``list`` 字段缺失或非 list → 返回空 (防御 API 改版)."""
    assert parse_status_list_json({}, base_url=_XUEQIU_BASE_URL) == []
    assert parse_status_list_json({"list": None}, base_url=_XUEQIU_BASE_URL) == []
    assert parse_status_list_json({"list": "not_a_list"}, base_url=_XUEQIU_BASE_URL) == []


def test_parse_skips_items_with_missing_fields() -> None:
    """单条 entry 字段缺失 → 跳过, 不影响其它行."""
    payload = _build_payload(
        [
            _build_item(title="", target="/p/empty"),  # 空 title
            _build_item(title="正常文章", target=""),  # 空 target
            {"title": "无 created_at", "target": "/p/no_time"},  # 无 created_at
            _build_item(title="正常文章 OK", target="/p/ok", created_at_ms=1714031234000),
        ]
    )
    out = parse_status_list_json(payload, base_url=_XUEQIU_BASE_URL)
    assert len(out) == 1
    assert out[0].title == "正常文章 OK"


def test_parse_strips_html_in_description() -> None:
    """description 含 HTML → 被 ``_strip_html`` 清理."""
    payload = _build_payload(
        [
            _build_item(
                title="测试 HTML",
                description="<p>多个 <br> 标签 <strong>测试</strong></p>",
            )
        ]
    )
    out = parse_status_list_json(payload, base_url=_XUEQIU_BASE_URL)
    assert len(out) == 1
    assert out[0].summary == "多个 标签 测试"


def test_parse_absolute_url_passthrough() -> None:
    """target 已是绝对 URL → 直接透传, 不再 join base_url."""
    payload = _build_payload(
        [
            _build_item(
                title="外站文章", target="https://other.example.com/foo/bar"
            )
        ]
    )
    out = parse_status_list_json(payload, base_url=_XUEQIU_BASE_URL)
    assert out[0].original_url == "https://other.example.com/foo/bar"


# ─── B. fetch_xueqiu_with_client (HTTP layer) ───────────────────────────


@pytest.mark.asyncio
@respx.mock(base_url=_XUEQIU_BASE_URL)
async def test_fetch_with_client_happy_multi_query(respx_mock: respx.Router) -> None:
    """200 + 多 query → 全部入结果, original_url 去重."""
    payload_q1 = _build_payload(
        [_build_item(title="文章 A", target="/p/A1", created_at_ms=1714031234000)]
    )
    payload_q2 = _build_payload(
        [
            # 同一 URL 在两 query 都命中, 应去重
            _build_item(title="文章 A", target="/p/A1", created_at_ms=1714031234000),
            _build_item(title="文章 B", target="/p/B2", created_at_ms=1714117634000),
        ]
    )
    # respx 按调用顺序回放
    respx_mock.get(XUEQIU_STATUS_SEARCH_PATH).mock(
        side_effect=[
            httpx.Response(200, json=payload_q1),
            httpx.Response(200, json=payload_q2),
        ]
    )

    async with httpx.AsyncClient(base_url=_XUEQIU_BASE_URL) as client:
        out = await fetch_xueqiu_with_client(
            client,
            base_url=_XUEQIU_BASE_URL,
            queries=["天星医疗", "腾讯"],
            count_per_query=20,
        )

    assert len(out) == 2
    titles = {a.title for a in out}
    assert titles == {"文章 A", "文章 B"}


@pytest.mark.asyncio
@respx.mock(base_url=_XUEQIU_BASE_URL)
async def test_fetch_with_client_5xx_skips_query(respx_mock: respx.Router) -> None:
    """5xx 跳过该 query, 不抛, 其它 query 仍能跑."""
    payload_q2 = _build_payload(
        [_build_item(title="正常文章", target="/p/normal", created_at_ms=1714031234000)]
    )
    respx_mock.get(XUEQIU_STATUS_SEARCH_PATH).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json=payload_q2),
        ]
    )

    async with httpx.AsyncClient(base_url=_XUEQIU_BASE_URL) as client:
        out = await fetch_xueqiu_with_client(
            client,
            base_url=_XUEQIU_BASE_URL,
            queries=["失败 query", "成功 query"],
            count_per_query=20,
        )

    assert len(out) == 1
    assert out[0].title == "正常文章"


@pytest.mark.asyncio
@respx.mock(base_url=_XUEQIU_BASE_URL)
async def test_fetch_with_client_network_error_skips(respx_mock: respx.Router) -> None:
    """``httpx.ConnectError`` 模拟网络层挂 → 跳过该 query, 不抛."""
    respx_mock.get(XUEQIU_STATUS_SEARCH_PATH).mock(
        side_effect=httpx.ConnectError("DNS down")
    )

    async with httpx.AsyncClient(base_url=_XUEQIU_BASE_URL) as client:
        out = await fetch_xueqiu_with_client(
            client,
            base_url=_XUEQIU_BASE_URL,
            queries=["any"],
            count_per_query=20,
        )

    assert out == []


@pytest.mark.asyncio
@respx.mock(base_url=_XUEQIU_BASE_URL)
async def test_fetch_with_client_invalid_json_skips(respx_mock: respx.Router) -> None:
    """雪球响应不是 JSON (反爬常见, 返 HTML 验证页) → warning + 跳过."""
    respx_mock.get(XUEQIU_STATUS_SEARCH_PATH).mock(
        return_value=httpx.Response(200, text="<html>captcha</html>")
    )

    async with httpx.AsyncClient(base_url=_XUEQIU_BASE_URL) as client:
        out = await fetch_xueqiu_with_client(
            client,
            base_url=_XUEQIU_BASE_URL,
            queries=["any"],
            count_per_query=20,
        )

    assert out == []
