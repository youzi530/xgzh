"""新浪财经滚动新闻 API 文章源 (BUG-S6.7-006 / spec/16 §Spike #3).

数据源选型记录
==============
新浪财经 ``feed.mix.sina.com.cn/api/roll/get`` 是新浪财经站的"滚动财经新闻"
API, 公开免登录, 一次返 50 条最新财经文章 (covering 沪深 A 股 / 美股 / 港股 /
基金 / 大宗商品 / 公司公告). 数据池**比按关键词搜索的 EM-search 大得多**, 但
噪音也多, 完全依赖 dispatcher 阶段的 :class:`IPOKeywordIndex` 反查筛选.

与 EM-search 形成互补:
- EM-search 精准 (按 keyword 搜) — 高 precision
- Sina 大池 (滚动新闻) — 高 recall, 抓周边新闻 / 行业动态 / 监管层评论 等

API endpoint
============
``GET https://feed.mix.sina.com.cn/api/roll/get``

::

    ?pageid=153
    &lid=2517         # lid=2517 = 全部股票市场新闻 (实测覆盖最广)
    &num=50           # 单次拉数, 上限 50
    &versionNumber=1.2.4

响应:

::

    {
      "result": {
        "status": {"code": 0, "msg": "ok"},
        "data": [
          {
            "title": "山西汾酒一季度报发布...",
            "intro": "(摘要 200 字)",
            "url": "https://finance.sina.com.cn/stock/roll/2026-04-29/doc-...shtml",
            "wapurl": "https://finance.sina.cn/2026-04-29/detail-...d.html",
            "intime": 1777454025,           # unix ts (UTC seconds)
            "media_name": "新浪证券",
            "keywords": "...",
            ...
          },
          ...
        ]
      }
    }

字段映射
========
- ``title`` → ``title``
- ``url`` (主) / ``wapurl`` (兜底) → ``original_url``
- ``intime`` (unix sec) → ``published_at`` (UTC)
- ``intro`` → ``summary``
- ``media_name`` → ``source_name``
- ``market = "BOTH"`` (新浪滚动 lid=2517 是全市场流, 港 A 美都有)
- ``source_credibility = 2`` (商业财经媒体, 中等公信力)
- ``is_full_text_available = True`` (新浪 finance 公开浏览)

测试性
======
- :func:`parse_sina_roll_response` 纯函数 (``dict -> list[ArticleRaw]``),
  fixture 直接喂 dict
- :func:`fetch_sina_with_client` 接外部 ``httpx.AsyncClient``, 让
  ``respx_mock`` 注入测试 client
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.services.article_ingest.sources.base import ArticleRaw

SINA_ROLL_URL = "https://feed.mix.sina.com.cn/api/roll/get"


def _parse_intime(raw: Any) -> datetime | None:
    """``intime`` 是 unix seconds (int 或 str), → UTC datetime.

    新浪偶尔返字符串型, 兼容. 解析失败 → None.
    """
    if raw is None:
        return None
    try:
        n = int(raw)
        # 防御: 1980-01-01 (315532800) ≤ ts ≤ 2100-01-01 (4102444800)
        if not 315532800 <= n <= 4102444800:
            return None
        return datetime.fromtimestamp(n, tz=UTC)
    except (TypeError, ValueError):
        return None


def parse_sina_roll_response(payload: dict[str, Any]) -> list[ArticleRaw]:
    """``dict`` (新浪 roll API JSON) → ``list[ArticleRaw]``.

    单条解析失败 → ``logger.debug`` skip + 继续.
    """
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    items = result.get("data", [])
    if not isinstance(items, list):
        return []

    out: list[ArticleRaw] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            title = (item.get("title") or "").strip()
            if not title:
                continue
            url = (item.get("url") or item.get("wapurl") or "").strip()
            if not url:
                continue
            published_at = _parse_intime(item.get("intime"))
            if not published_at:
                # 实在没 intime → 用 ctime / mtime 兜底
                published_at = (
                    _parse_intime(item.get("ctime"))
                    or _parse_intime(item.get("mtime"))
                )
            if not published_at:
                continue
            summary = item.get("intro")
            summary = summary.strip() if isinstance(summary, str) and summary.strip() else None
            media_name = (item.get("media_name") or "新浪财经").strip() or "新浪财经"

            out.append(
                ArticleRaw(
                    title=title,
                    original_url=url,
                    source_name=media_name,
                    published_at=published_at,
                    summary=summary,
                    market="BOTH",
                    source_credibility=2,
                    is_full_text_available=True,
                )
            )
        except Exception as e:  # noqa: BLE001 — 单条 fail-soft
            logger.debug(f"sina_finance.parse_item_failed: {e}")
            continue
    return out


# ─── HTTP layer ─────────────────────────────────────────────────────


async def fetch_sina_with_client(
    client: httpx.AsyncClient,
    *,
    pageid: int = 153,
    lid: int = 2517,
    num: int = 50,
    request_timeout: float = 10.0,
) -> list[ArticleRaw]:
    """走外部 ``httpx.AsyncClient`` 拉新浪滚动新闻 1 次 → ``list[ArticleRaw]``.

    fail-soft: HTTP / 网络 / parse 失败一律返 [] 不抛.
    """
    try:
        resp = await client.get(
            SINA_ROLL_URL,
            params={
                "pageid": pageid,
                "lid": lid,
                "num": num,
                "versionNumber": "1.2.4",
            },
            timeout=request_timeout,
        )
    except (TimeoutError, httpx.HTTPError) as e:
        logger.warning(
            f"sina_finance.fetch_failed: {type(e).__name__}: {e}"
        )
        return []
    except Exception as e:  # noqa: BLE001
        logger.exception(f"sina_finance.fetch_unexpected: {e}")
        return []

    if resp.status_code >= 500:
        logger.warning(f"sina_finance.fetch 5xx status={resp.status_code}")
        return []
    if resp.status_code >= 400:
        logger.warning(f"sina_finance.fetch 4xx status={resp.status_code}")
        return []

    try:
        payload = resp.json()
    except ValueError as e:
        logger.warning(f"sina_finance.fetch_json_invalid: {e}")
        return []

    return parse_sina_roll_response(payload)


class SinaFinanceClient:
    """``ArticleSource`` 实现: 新浪财经滚动新闻 API.

    与 :class:`ZhitongRSSClient` 一样 — 拉一次大池, 不接 keyword. dispatcher
    阶段统一走 :class:`IPOKeywordIndex` 反查筛选.

    ``lid`` 是分类 ID, 默认 2517 实测是"全部股票市场新闻", 覆盖最广.
    """

    name: str = "新浪财经"

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def fetch(self, *, since: datetime | None = None) -> list[ArticleRaw]:
        s = self._settings
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
            ),
            "Accept": "application/json",
            "Referer": "https://finance.sina.com.cn/",
        }
        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=s.article_ingest_request_timeout_seconds,
        ) as client:
            return await fetch_sina_with_client(
                client,
                pageid=s.article_ingest_sina_pageid,
                lid=s.article_ingest_sina_lid,
                num=s.article_ingest_sina_num,
                request_timeout=s.article_ingest_request_timeout_seconds,
            )


__all__ = [
    "SINA_ROLL_URL",
    "SinaFinanceClient",
    "fetch_sina_with_client",
    "parse_sina_roll_response",
]
