"""长桥证券社区帖子文章源 (BUG-S8-003 / spec/23 §C 类 ⏳ 推荐).

数据源选型记录
==============
Sprint 7.2 spike v2 重大发现 — 长桥 OpenAPI **港股唯一开放投资者社区 API**
(``/v1/community/posts``). Sprint 7.3 已落地 ``LongbridgeApiClient`` (新闻 API,
``/v1/quote/news``), 共用同一 access_token, 加社区源**边际成本为 0**.

与 :class:`LongbridgeApiClient` 的关键区别
=========================================
- 新闻 API: 持牌媒体转载 / 公司公告 → ``source_name = "长桥·<原 source>"``
  → FE "持牌媒体" tab
- **社区 API: 投资者用户发帖 / 评论** → ``source_name = "长桥社区·<作者>"``
  → FE **"大V点评"** tab (与微信公众号 ``"微信·"`` 同分流)

合规: 长桥 OpenAPI 是官方授权, 数据使用受长桥用户协议约束; 社区帖子是**用户公开
发布的内容**, 本 client 仅订阅公开 stream, 不抓取私域内容, 与 Twitter / Reddit 公
开 API 同合规.

token 未配置时的行为 (与 LongbridgeApiClient 同款 token-gated)
=================================================================
- ``LONGBRIDGE_API_TOKEN`` 留空 (默认): client ``is_enabled=False``, fetch 立即返
  [], 不发任何 HTTP 请求. dispatcher 注册时也跳过.
- 用户填 token 后**自动同时启用**新闻 + 社区两源, 不需重新发版

API endpoint
============
默认 ``/v1/community/posts`` (实际路径以官方文档为准, 用户拿 token 实测后调整).
路径通过 ``settings.longbridge_community_path`` 配置, 与 ``longbridge_api_news_path``
独立, 让用户拿到 token 后只调一处即可.

字段映射 (推测 schema, 与新闻 API 字段相似)
============================================
- ``title`` / ``content`` 任一非空 → ``ArticleRaw.title`` (社区帖子可能纯内容
  无标题, 与财联社快讯同处理: 标题空 → 内容首句作 title fallback)
- ``content`` / ``summary`` → ``summary``
- ``link`` / ``url`` → ``original_url``
- ``author`` / ``user_name`` / ``nickname`` → 包前缀 ``"长桥社区·"`` → source_name
- ``published_at`` (unix s/ms) → datetime UTC
- ``market = "HK"`` 长桥主战场港股
- ``source_credibility = 2`` 用户社区帖子, 中等公信力 (比持牌媒体 3 低 1)
- ``is_full_text_available = True`` 长桥社区允许 webview 渲染

测试性
======
- :func:`parse_longbridge_community_json` 纯函数, fixture 直接喂 JSON
- :func:`fetch_longbridge_community_with_client` 接外部 client, respx 注入
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.services.article_ingest.sources.base import ArticleRaw
from app.services.article_ingest.sources.longbridge_api_client import (
    DEFAULT_LONGBRIDGE_BASE_URL,
)

LONGBRIDGE_COMMUNITY_PATH_DEFAULT = "/v1/community/posts"

# 数据源前缀 — FE "大V点评" tab 按 ``startsWith("长桥社区·")`` 与 "微信·" 一同分流
LONGBRIDGE_COMMUNITY_SOURCE_PREFIX = "长桥社区·"


def _parse_unix_to_utc(ts: Any) -> datetime | None:
    """与 longbridge_api_client 同款实现: unix 秒/毫秒 → datetime UTC."""
    if ts is None:
        return None
    try:
        n = int(ts)
    except (TypeError, ValueError):
        return None
    if 315532800 <= n <= 4102444800:
        return datetime.fromtimestamp(n, tz=UTC)
    if 315532800_000 <= n <= 4102444800_000:
        return datetime.fromtimestamp(n / 1000, tz=UTC)
    return None


def _truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def parse_longbridge_community_json(
    payload: dict[str, Any], *, symbol: str | None = None
) -> list[ArticleRaw]:
    """长桥社区 API JSON → ``list[ArticleRaw]``.

    宽松解析: 字段名按 ``data.list`` / ``data.posts`` / ``data.items`` 兼容, 单条
    解析失败 skip + log.
    """
    if not isinstance(payload, dict):
        return []

    items: list[Any] = []
    data = payload.get("data")
    if isinstance(data, dict):
        for k in ("list", "posts", "items"):
            v = data.get(k)
            if isinstance(v, list):
                items = v
                break
    if not items:
        v = payload.get("list") or payload.get("posts")
        if isinstance(v, list):
            items = v

    out: list[ArticleRaw] = []
    seen_urls: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            content_raw = (
                it.get("content") or it.get("text") or it.get("body") or ""
            ).strip()
            title_raw = (it.get("title") or "").strip()
            # 标题或内容至少一个非空; 都空 skip
            if not title_raw and not content_raw:
                continue
            title = title_raw or _truncate(content_raw, 30)
            if not title:
                continue

            url = (
                it.get("link")
                or it.get("url")
                or it.get("post_url")
                or ""
            ).strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            published_at = _parse_unix_to_utc(
                it.get("published_at")
                or it.get("publish_time")
                or it.get("publishTime")
                or it.get("created_at")
                or it.get("time")
            )
            if not published_at:
                continue

            author_raw = (
                it.get("author")
                or it.get("user_name")
                or it.get("nickname")
                or it.get("user")
                or "匿名"
            )
            if isinstance(author_raw, dict):
                # 部分接口 user 是嵌套对象 {name: "..."}
                author_raw = (
                    author_raw.get("name")
                    or author_raw.get("nickname")
                    or "匿名"
                )
            author = (str(author_raw) or "匿名").strip() or "匿名"

            summary = content_raw if content_raw else None
            if isinstance(summary, str) and len(summary) > 200:
                summary = _truncate(summary, 200)

            out.append(
                ArticleRaw(
                    title=_truncate(title, 100),
                    original_url=url,
                    source_name=f"{LONGBRIDGE_COMMUNITY_SOURCE_PREFIX}{author}",
                    published_at=published_at,
                    summary=summary,
                    market="HK",
                    source_credibility=2,
                    is_full_text_available=True,
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(
                f"longbridge_community.parse_item_failed symbol={symbol!r}: {e}"
            )
            continue
    return out


# ─── HTTP layer ─────────────────────────────────────────────────────


async def fetch_longbridge_community_with_client(
    client: httpx.AsyncClient,
    *,
    symbols: list[str],
    base_url: str = DEFAULT_LONGBRIDGE_BASE_URL,
    community_path: str = LONGBRIDGE_COMMUNITY_PATH_DEFAULT,
    semaphore: asyncio.Semaphore | None = None,
    request_timeout: float = 10.0,
    inter_query_delay_seconds: float = 0.2,
) -> list[ArticleRaw]:
    """走外部 ``httpx.AsyncClient`` 跑 N 个 HK symbol 拉社区帖子, 合并去重.

    单 symbol 失败 → ``logger.warning`` skip; 401/403 → 中止整批 (token 失效).

    ``client.headers`` 应已包含 ``Authorization: Bearer <token>``.
    """
    sem = semaphore or asyncio.Semaphore(2)
    seen_urls: set[str] = set()
    out: list[ArticleRaw] = []

    url = base_url.rstrip("/") + community_path

    for idx, symbol in enumerate(symbols):
        if not symbol:
            continue
        if idx > 0 and inter_query_delay_seconds > 0:
            await asyncio.sleep(inter_query_delay_seconds)
        try:
            async with sem:
                resp = await client.get(
                    url,
                    params={"symbol": symbol},
                    timeout=request_timeout,
                )
            if resp.status_code in (401, 403):
                logger.warning(
                    f"longbridge_community.unauthorized symbol={symbol} "
                    f"status={resp.status_code} (token 失效, 中止本批)"
                )
                break
            if resp.status_code >= 500:
                logger.warning(
                    f"longbridge_community.fetch 5xx symbol={symbol} "
                    f"status={resp.status_code}"
                )
                continue
            if resp.status_code >= 400:
                logger.warning(
                    f"longbridge_community.fetch 4xx symbol={symbol} "
                    f"status={resp.status_code}"
                )
                continue
            try:
                payload = resp.json()
            except ValueError as e:
                logger.warning(
                    f"longbridge_community.json_parse_failed symbol={symbol}: {e}"
                )
                continue
            for art in parse_longbridge_community_json(payload, symbol=symbol):
                if art.original_url in seen_urls:
                    continue
                seen_urls.add(art.original_url)
                out.append(art)
        except (TimeoutError, httpx.HTTPError) as e:
            logger.warning(
                f"longbridge_community.fetch_failed symbol={symbol}: "
                f"{type(e).__name__}: {e}"
            )
            continue
        except Exception as e:  # noqa: BLE001
            logger.exception(
                f"longbridge_community.fetch_unexpected symbol={symbol}: {e}"
            )
            continue

    return out


class LongbridgeCommunityClient:
    """``ArticleSource`` 实现: 长桥证券投资者社区帖子.

    与 :class:`LongbridgeApiClient` (新闻) 共享同一 access_token, token 配置时
    **同时启用** 两源, 0 边际成本. 与新闻不同的关键定位:

    - 新闻 → 持牌媒体 tab (``长桥·`` 前缀)
    - **社区 → 大V点评 tab** (``长桥社区·`` 前缀, 与微信公众号 ``微信·`` 同分流)

    版权:
    - 长桥 OpenAPI 官方授权, 公开 stream, 合规
    - ``source_credibility = 2`` 用户社区帖子, 中等公信力
    - ``is_full_text_available = True`` 长桥 link 允许 webview 渲染
    """

    name: str = "长桥社区"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        symbols: list[str],
    ) -> None:
        self._settings = settings or get_settings()
        self._symbols = symbols

    @property
    def is_enabled(self) -> bool:
        """与新闻 API 同款 token-gated; token 配了才启用."""
        return bool(self._settings.longbridge_api_token)

    async def fetch(self, *, since: datetime | None = None) -> list[ArticleRaw]:
        if not self.is_enabled:
            logger.debug("longbridge_community.skipped — token 未配置")
            return []
        if not self._symbols:
            return []
        s = self._settings
        headers = {
            "Authorization": f"Bearer {s.longbridge_api_token}",
            "Accept": "application/json",
            "User-Agent": "xgzh-api/1.0 (+article_ingest)",
        }
        sem = asyncio.Semaphore(s.article_ingest_request_concurrency)
        symbols = self._symbols[: s.longbridge_api_max_queries]
        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=s.article_ingest_request_timeout_seconds,
        ) as client:
            return await fetch_longbridge_community_with_client(
                client,
                symbols=symbols,
                base_url=s.longbridge_api_base_url,
                community_path=s.longbridge_community_path,
                semaphore=sem,
                request_timeout=s.article_ingest_request_timeout_seconds,
                inter_query_delay_seconds=s.longbridge_api_inter_query_delay_seconds,
            )


__all__ = [
    "LONGBRIDGE_COMMUNITY_PATH_DEFAULT",
    "LONGBRIDGE_COMMUNITY_SOURCE_PREFIX",
    "LongbridgeCommunityClient",
    "fetch_longbridge_community_with_client",
    "parse_longbridge_community_json",
]
