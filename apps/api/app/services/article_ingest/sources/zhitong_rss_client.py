"""智通财经 RSS 数据源 (BE-S3-002).

数据源选型记录
==============
智通财经 (zhitongcaijing.com) 是港股专项新闻站, 覆盖 IPO / 年报 / 行业研究等;
公开 RSS feed 一般在 ``/rss/news.xml`` (各栏目可配, 不同环境差异).

测试性
======
- ``parse_rss_feed`` 纯函数 (``str -> list[ArticleRaw]``), 单测 fixture 喂 RSS
  XML 字符串
- ``ZhitongRSSClient.fetch`` 接 settings + httpx; 单测用 ``respx_mock`` 拦请求

为什么 feedparser 走 thread executor
====================================
``feedparser`` 是同步纯 Python 库 (~12k 行, parse 复杂 XML / Atom / RSS 1.x/2.0),
没 async 版本. 一篇 RSS feed (50-100 entries) 解析在 5-50ms 之间, 直接在 event
loop 里跑会偶发卡 hot path. ``asyncio.to_thread`` 丢线程池 (sync 操作 < 100ms
的事实标准做法), 不增加复杂度.

版权合规
========
``is_full_text_available=False``: 智通 RSS 仅授权摘要 (200 字内), 不授权全文
转载. FE 渲染时显示 "查看全文 →" 按钮跳外链 (spec/03 §模块二 §版权合规).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import struct_time
from typing import Any

import feedparser
import httpx

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.services.article_ingest.sources.base import ArticleRaw


def _struct_time_to_utc(t: struct_time | None) -> datetime | None:
    """feedparser 返回的 ``published_parsed`` 是 ``time.struct_time`` (UTC).

    None / 解析失败 → None; 调用方再决定丢弃还是兜底.
    """
    if not t:
        return None
    try:
        return datetime(*t[:6], tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def parse_rss_feed(
    xml_text: str, *, source_name: str = "智通财经"
) -> list[ArticleRaw]:
    """RSS / Atom XML → ``list[ArticleRaw]``.

    用 ``feedparser`` 自动识别 RSS 2.0 / Atom / RSS 1.x. 单 entry 解析失败
    → ``logger.debug`` skip + 继续 (与 xueqiu / hkex 一致 fail-soft).

    feedparser 不抛异常 — 解析失败时 ``feed.bozo == 1`` 但仍尝试返部分数据;
    本层不强校验 ``bozo`` (兼容性优先), 单 entry 字段缺失才丢.
    """
    if not xml_text:
        return []

    parsed = feedparser.parse(xml_text)
    entries = parsed.get("entries", []) if isinstance(parsed, dict) else []
    if not entries:
        return []

    out: list[ArticleRaw] = []
    for entry in entries:
        try:
            entry_dict: dict[str, Any] = entry if isinstance(entry, dict) else {}
            title = (entry_dict.get("title") or "").strip()
            link = (entry_dict.get("link") or "").strip()
            if not title or not link:
                continue
            published_at = _struct_time_to_utc(entry_dict.get("published_parsed"))
            if not published_at:
                # 部分 RSS 用 updated 而非 published
                published_at = _struct_time_to_utc(entry_dict.get("updated_parsed"))
            if not published_at:
                # 实在没时间 → 用 now (RSS 缺 pubDate 极少, 兜底防丢条)
                published_at = datetime.now(UTC)

            summary = entry_dict.get("summary") or entry_dict.get("description")
            summary = summary.strip() if isinstance(summary, str) else None

            out.append(
                ArticleRaw(
                    title=title,
                    original_url=link,
                    source_name=source_name,
                    published_at=published_at,
                    summary=summary,
                    market="HK",  # 智通主打港股, 默认 HK; dispatcher 关键词反查再细化
                    source_credibility=3,  # 持牌财经媒体, 公信力高
                    is_full_text_available=False,  # RSS 只授权摘要
                )
            )
        except Exception as e:  # noqa: BLE001 — 单条 fail-soft
            logger.debug(f"zhitong_rss.parse_entry_failed: {e}")
            continue
    return out


# ─── HTTP layer ──────────────────────────────────────────────────────


async def fetch_zhitong_with_client(
    client: httpx.AsyncClient,
    *,
    rss_url: str,
    request_timeout: float = 10.0,
) -> list[ArticleRaw]:
    """走外部 ``httpx.AsyncClient`` 拉 RSS XML + ``feedparser`` 解析.

    fail-soft: HTTP 失败 / 网络异常 / parse 异常一律返 [] (与 xueqiu 同款).
    """
    try:
        resp = await client.get(rss_url, timeout=request_timeout)
    except (TimeoutError, httpx.HTTPError) as e:
        logger.warning(f"zhitong_rss.fetch_failed: {type(e).__name__}: {e}")
        return []
    except Exception as e:  # noqa: BLE001
        logger.exception(f"zhitong_rss.fetch_unexpected: {e}")
        return []

    if resp.status_code >= 500:
        logger.warning(f"zhitong_rss.fetch 5xx status={resp.status_code}")
        return []
    if resp.status_code >= 400:
        logger.warning(f"zhitong_rss.fetch 4xx status={resp.status_code}")
        return []

    # feedparser 是同步; 丢 thread pool 防止阻塞 event loop
    try:
        return await asyncio.to_thread(parse_rss_feed, resp.text)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"zhitong_rss.parse_unexpected: {e}")
        return []


class ZhitongRSSClient:
    """``ArticleSource`` 实现: 智通财经 RSS feed.

    ``fetch()`` 不带 since 过滤 — RSS 自带最近 N 条, since 由 dispatcher 写库
    ``ON CONFLICT DO NOTHING`` 兜底.
    """

    name: str = "智通财经"

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def fetch(self, *, since: datetime | None = None) -> list[ArticleRaw]:
        s = self._settings
        if not s.zhitong_rss_url:
            logger.debug("zhitong_rss.fetch_skipped (ZHITONG_RSS_URL empty)")
            return []
        headers = {
            "User-Agent": "xgzh-api/0.1 (+https://xgzh.example.com; contact: ops@xgzh)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        }
        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=s.article_ingest_request_timeout_seconds,
        ) as client:
            return await fetch_zhitong_with_client(
                client,
                rss_url=s.zhitong_rss_url,
                request_timeout=s.article_ingest_request_timeout_seconds,
            )


__all__ = [
    "ZhitongRSSClient",
    "fetch_zhitong_with_client",
    "parse_rss_feed",
]
