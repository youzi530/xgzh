"""雪球公开 API 数据源 (BE-S3-002).

数据源选型记录
==============
雪球公开端点对比 (踩坑实录):

| endpoint | 状态 | 说明 |
|----------|------|------|
| ``/query/v1/symbol/search/status.json?q=&count=`` | 选用 | 按关键词搜分享流, 命中 IPO 名 / 代码即返;  ``q`` 必填, 配 IPO 关键词集做 N 次查 |
| ``/statuses/hot/listV2.json``                     | 弃用 | 全市场热帖, 没法限定 IPO; 噪音过大 |
| ``/v4/statuses/public_timeline_by_category.json`` | 弃用 | 同上, 且需要登录 cookie |

单次反爬阈值: 实测约 5 req/s 后偶发 503; ``Semaphore(N)`` + 1s 超时 buffer
+ ``User-Agent`` 显式标识 (User 端策略友好), 默认 N=2 已够安全.

JSON 响应字段 (踩坑实录)
========================
``/query/v1/symbol/search/status.json`` 返回结构 (2026-04 实测):

::

    {
      "list": [
        {
          "id": 268938472,
          "user_id": 1234,
          "user": {"screen_name": "...", "profile_image_url": "..."},
          "created_at": 1714031234000,    # ms timestamp, UTC
          "title": "天星医疗 IPO 定价 21.6 元 / 股, 募资 25 亿",
          "description": "<p>(原文 200 字摘要 HTML)</p>",
          "target": "/zhuanlan/12345",   # 相对 URL, 拼 base_url
          "view_count": 5000,
          "reply_count": 30,
          "like_count": 80
        },
        ...
      ],
      "next_max_id": 268938400,           # 翻页用 (本 PR 不翻页)
      "count": 20
    }

字段都是 defensive 处理: 缺失 → ``None`` / 跳过.

测试性
======
- ``parse_status_list_json`` 纯函数 (``dict -> list[ArticleRaw]``), 单测 fixture
  直接喂 dict
- ``XueqiuClient.fetch`` 接 settings + httpx; 单测用 ``respx_mock`` 拦请求
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.services.article_ingest.sources.base import ArticleRaw

XUEQIU_STATUS_SEARCH_PATH = "/query/v1/symbol/search/status.json"
"""雪球关键词搜索 endpoint (按 q + count 拉)."""

_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
"""粗暴去 HTML; description 字段返的是带 ``<p>`` / ``<br>`` 的简易 HTML,
完整解析没必要, BE-S3-005 LLM TL;DR 阶段会重新清洗."""


def _strip_html(text: str | None) -> str | None:
    """去 HTML 标签 + 折叠空白; ``None`` 透传."""
    if not text:
        return None
    cleaned = _HTML_TAG_PATTERN.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _parse_published_at(created_at_ms: Any) -> datetime | None:
    """雪球 ``created_at`` 是 UTC ms timestamp; 容错 None / 非数 → None."""
    if created_at_ms is None:
        return None
    try:
        ts = int(created_at_ms) / 1000
        return datetime.fromtimestamp(ts, tz=UTC)
    except (TypeError, ValueError):
        return None


def _build_url(base_url: str, target: str | None) -> str | None:
    """target 一般是 ``/p/12345`` 这样的相对路径; base_url 拼一下."""
    if not target:
        return None
    if target.startswith("http://") or target.startswith("https://"):
        return target
    return urljoin(base_url, target)


def _hot_score(item: dict[str, Any]) -> Decimal:
    """``view + 3 * reply + 5 * like`` 简单加权热度; 缺字段 → 0.

    系数选择: like > reply > view 的权重分布 (用户主动行为 > 被动浏览),
    与 spec/04 §推荐排序里的简化版本一致. 后续 BE-S3-006 详细排序会再换.
    """
    view = int(item.get("view_count") or 0)
    reply = int(item.get("reply_count") or 0)
    like = int(item.get("like_count") or 0)
    return Decimal(view + reply * 3 + like * 5)


def parse_status_list_json(
    payload: dict[str, Any], *, base_url: str
) -> list[ArticleRaw]:
    """把雪球 ``/status.json`` JSON 响应解析成 ``ArticleRaw`` 列表.

    单条解析失败 → ``logger.debug`` skip, 不影响其它行 (与 hkex_client 同款
    fail-soft 策略).
    """
    raw_list = payload.get("list") if isinstance(payload, dict) else None
    if not isinstance(raw_list, list):
        logger.debug("xueqiu.parse: 'list' field missing or not array")
        return []

    out: list[ArticleRaw] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        try:
            title = (item.get("title") or "").strip()
            if not title:
                continue
            target = item.get("target")
            url = _build_url(base_url, target)
            if not url:
                continue
            published_at = _parse_published_at(item.get("created_at"))
            if not published_at:
                # 雪球极少缺 created_at, 没有就丢 — 入库 NOT NULL 兜不住
                continue
            summary = _strip_html(item.get("description"))
            out.append(
                ArticleRaw(
                    title=title,
                    original_url=url,
                    source_name="雪球",
                    published_at=published_at,
                    summary=summary,
                    market="BOTH",
                    source_credibility=2,
                    is_full_text_available=True,
                    hot_score=_hot_score(item),
                )
            )
        except Exception as e:  # noqa: BLE001 — 单条 fail-soft
            logger.debug(f"xueqiu.parse_item_failed: {e}")
            continue
    return out


# ─── HTTP layer ──────────────────────────────────────────────────────


async def fetch_xueqiu_with_client(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    queries: list[str],
    count_per_query: int = 20,
    semaphore: asyncio.Semaphore | None = None,
    request_timeout: float = 10.0,
) -> list[ArticleRaw]:
    """走外部 ``httpx.AsyncClient`` 跑 N 条关键词查询并合并去重.

    每个 query 是一个 IPO 关键词 (例 ``天星医疗`` / ``02501``); ``Semaphore``
    限并发避免触发雪球反爬. 单 query 失败 → warning + 跳过, 不影响其它 query
    (与 hkex_client.fetch_hk_applicants_with_client 同款 fail-soft).

    去重: 按 ``original_url`` 取首条 (来源相同 query 多次命中常见).
    """
    sem = semaphore or asyncio.Semaphore(2)
    seen_urls: set[str] = set()
    out: list[ArticleRaw] = []

    for q in queries:
        if not q:
            continue
        url = urljoin(base_url, XUEQIU_STATUS_SEARCH_PATH)
        try:
            async with sem:
                resp = await client.get(
                    url,
                    params={"q": q, "count": count_per_query, "source": "user"},
                    timeout=request_timeout,
                )
            if resp.status_code >= 500:
                logger.warning(
                    f"xueqiu.fetch 5xx q={q} status={resp.status_code}"
                )
                continue
            if resp.status_code >= 400:
                logger.warning(
                    f"xueqiu.fetch 4xx q={q} status={resp.status_code}"
                )
                continue
            try:
                payload = resp.json()
            except ValueError as e:
                logger.warning(f"xueqiu.fetch_json_invalid q={q}: {e}")
                continue
            for art in parse_status_list_json(payload, base_url=base_url):
                if art.original_url in seen_urls:
                    continue
                seen_urls.add(art.original_url)
                out.append(art)
        except (TimeoutError, httpx.HTTPError) as e:
            logger.warning(
                f"xueqiu.fetch_failed q={q}: {type(e).__name__}: {e}"
            )
            continue
        except Exception as e:  # noqa: BLE001
            logger.exception(f"xueqiu.fetch_unexpected q={q}: {e}")
            continue

    return out


class XueqiuClient:
    """``ArticleSource`` 实现: 雪球公开 status 搜索接口.

    构造时接受 settings + 关键词列表 (一般由 dispatcher 从 IPOKeywordIndex 拿
    每只 IPO 的首选关键词). ``fetch()`` 不带 since 过滤 — 雪球 API 返的就是
    最近 N 条, since 由 dispatcher 写库时 ``ON CONFLICT DO NOTHING`` 兜底.
    """

    name: str = "雪球"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        queries: list[str],
    ) -> None:
        self._settings = settings or get_settings()
        self._queries = queries

    async def fetch(self, *, since: datetime | None = None) -> list[ArticleRaw]:
        if not self._queries:
            return []
        s = self._settings
        headers = {
            "User-Agent": "xgzh-api/0.1 (+https://xgzh.example.com; contact: ops@xgzh)",
            "Accept": "application/json",
        }
        sem = asyncio.Semaphore(s.article_ingest_request_concurrency)
        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=s.article_ingest_request_timeout_seconds,
        ) as client:
            return await fetch_xueqiu_with_client(
                client,
                base_url=s.xueqiu_base_url,
                queries=self._queries,
                count_per_query=s.article_ingest_xueqiu_count_per_query,
                semaphore=sem,
                request_timeout=s.article_ingest_request_timeout_seconds,
            )


__all__ = [
    "XUEQIU_STATUS_SEARCH_PATH",
    "XueqiuClient",
    "fetch_xueqiu_with_client",
    "parse_status_list_json",
]
