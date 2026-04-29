"""长桥证券 OpenAPI 文章源 (BUG-S7.3-001 / spec/21 §大V 替代源 spike v2 推荐).

数据源选型记录
==============
Sprint 6.9 接入搜狗微信后, 用户复测 (bug-fix-21:53) 反馈搜狗反爬触发频繁,
要求继续 spike 替代源. Sprint 7.2 spike v2 重大发现:

    长桥证券 OpenAPI **完全免费** + 港股新闻 + **社区/帖子 API**

是 6.7-6.9 spike 全程 0 命中过的"投资者社区开放接口" — 长桥用户群体本身
就是港股 / 美股 active trader, 打新讨论是核心场景.

Sprint 7.3 用户拍板 ``A 框架先行``: 写 client 完整代码 + 配置 token=空时跳过,
等用户走完长桥开户 + OpenAPI 申请 + OAuth (15-30min 操作) 拿到 access_token
填 ``LONGBRIDGE_API_TOKEN`` 即可立即生效, 不需重新发版.

API endpoint
============
长桥 API 实际路径以官方文档为准 (Sprint 7.3 spike 时 API 文档站超时, 字段
尚需用户拿 token 后实测). 默认配置 (可在 ``.env`` 改):

- 新闻 API: ``GET <base>/v1/quote/news?symbol=<HK_CODE>``
- 社区 API: ``GET <base>/v1/community/posts?symbol=<HK_CODE>`` (假设, 用户实测确认)

base URL: ``https://openapi.longbridge.global``

字段映射 (基于公开文档示例)
============================
- ``title`` → ``ArticleRaw.title``
- ``summary`` → ``ArticleRaw.summary``
- ``link`` → ``original_url``
- ``source`` → 包前缀 ``"长桥·"`` → ``source_name`` (例 "长桥·证券日报",
  与"微信·"前缀对齐, FE 可加二级 chip 区分长桥/微信/持牌媒体)
- ``published_at`` (unix 秒) → datetime UTC
- ``comment_count + like_count + share_count`` → 暂不映射 (未来 v2 给
  ``hot_score`` 加权)

合规
====
- 长桥 OpenAPI 是官方授权接口, 数据使用受长桥用户协议约束
- ``source_credibility = 3``: 长桥是持牌券商, 公信力等同持牌媒体
- ``is_full_text_available = True``: 长桥新闻 API 返回 link 可 inline 渲染
  (与雪球长文等同). 社区帖子 link 也允许 webview 打开

token 未配置时的行为
====================
- ``LONGBRIDGE_API_TOKEN`` 留空 (默认): client 实例化后 ``fetch()`` 直接返 [],
  不发任何 HTTP 请求. dispatcher 注册时也会跳过 (见 dispatcher.register_sources).
- 这让 Sprint 7.3 上线时**不依赖**用户立即拿到 token; 用户后续填 token
  即自动启用, 无需改代码.

测试性
======
- :func:`parse_longbridge_news_json` 纯函数 (``dict -> list[ArticleRaw]``)
- :func:`fetch_longbridge_with_client` 接外部 ``httpx.AsyncClient``, respx 注入
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.services.article_ingest.sources.base import ArticleRaw

DEFAULT_LONGBRIDGE_BASE_URL = "https://openapi.longbridge.global"
LONGBRIDGE_NEWS_PATH = "/v1/quote/news"
LONGBRIDGE_COMMUNITY_PATH = "/v1/community/posts"

# 数据源前缀 — FE 按 ``source_name.startsWith("长桥·")`` filter
LONGBRIDGE_SOURCE_PREFIX = "长桥·"


def _parse_unix_to_utc(ts: Any) -> datetime | None:
    """unix 秒 / 毫秒 → datetime UTC. 解析失败返 None.

    长桥文档同时见过秒级(10 位) + 毫秒级(13 位) 两种格式, 这里都接.
    """
    if ts is None:
        return None
    try:
        n = int(ts)
    except (TypeError, ValueError):
        return None
    # 1980-01-01 (315532800 秒) ≤ n ≤ 2100-01-01 (4102444800 秒) → 秒级
    if 315532800 <= n <= 4102444800:
        return datetime.fromtimestamp(n, tz=UTC)
    # 毫秒级
    if 315532800_000 <= n <= 4102444800_000:
        return datetime.fromtimestamp(n / 1000, tz=UTC)
    return None


def parse_longbridge_news_json(
    payload: dict[str, Any], *, symbol: str | None = None
) -> list[ArticleRaw]:
    """长桥新闻 API JSON → ``list[ArticleRaw]``.

    宽松解析: 字段缺失走 None / 默认值, 不抛. 长桥 API 字段名以实际响应为准
    (Sprint 7.3 token 申请后用户实测调整).

    支持的常见 schema (覆盖文档示例 + 推测):
    - ``payload["data"]["list"]`` (官方推荐)
    - ``payload["data"]["news"]``
    - ``payload["list"]``
    """
    if not isinstance(payload, dict):
        return []

    items: list[Any] = []
    data = payload.get("data")
    if isinstance(data, dict):
        for k in ("list", "news", "items"):
            v = data.get(k)
            if isinstance(v, list):
                items = v
                break
    if not items:
        v = payload.get("list")
        if isinstance(v, list):
            items = v

    out: list[ArticleRaw] = []
    seen_urls: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            title = (it.get("title") or it.get("headline") or "").strip()
            if not title:
                continue
            url = (
                it.get("link")
                or it.get("url")
                or it.get("news_link")
                or ""
            ).strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            published_at = _parse_unix_to_utc(
                it.get("published_at")
                or it.get("publish_time")
                or it.get("publishTime")
                or it.get("time")
            )
            if not published_at:
                continue
            source_raw = (
                it.get("source") or it.get("source_name") or "长桥"
            ).strip() or "长桥"
            source_name = f"{LONGBRIDGE_SOURCE_PREFIX}{source_raw}"
            summary = (
                it.get("summary") or it.get("description") or it.get("brief") or None
            )
            if isinstance(summary, str):
                summary = summary.strip() or None
            out.append(
                ArticleRaw(
                    title=title,
                    original_url=url,
                    source_name=source_name,
                    published_at=published_at,
                    summary=summary if isinstance(summary, str) else None,
                    market="HK",
                    source_credibility=3,
                    is_full_text_available=True,
                )
            )
        except Exception as e:  # noqa: BLE001 — 单条 fail-soft
            logger.debug(
                f"longbridge_api.parse_item_failed symbol={symbol!r}: {e}"
            )
            continue
    return out


# ─── HTTP layer ─────────────────────────────────────────────────────


async def fetch_longbridge_with_client(
    client: httpx.AsyncClient,
    *,
    symbols: list[str],
    base_url: str = DEFAULT_LONGBRIDGE_BASE_URL,
    news_path: str = LONGBRIDGE_NEWS_PATH,
    semaphore: asyncio.Semaphore | None = None,
    request_timeout: float = 10.0,
    inter_query_delay_seconds: float = 0.2,
) -> list[ArticleRaw]:
    """走外部 ``httpx.AsyncClient`` 跑 N 个 HK symbol 拉新闻, 合并去重.

    长桥 API 速率限制 10 次/秒 (官方文档明示), 比搜狗微信宽松 1 个量级;
    ``inter_query_delay_seconds`` 默认 0.2s 保守覆盖, ``Semaphore`` 限并发.

    单 symbol 失败 → ``logger.warning`` skip + 继续. 去重: 按 ``original_url``.

    ``client.headers`` 应已包含 ``Authorization: Bearer <token>`` (调用方
    构造 client 时设置).
    """
    sem = semaphore or asyncio.Semaphore(2)
    seen_urls: set[str] = set()
    out: list[ArticleRaw] = []

    url = base_url.rstrip("/") + news_path

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
            if resp.status_code == 401 or resp.status_code == 403:
                # token 失效 / 未授权 → 中止整批 (后续 symbol 也会失败)
                logger.warning(
                    f"longbridge_api.unauthorized symbol={symbol} "
                    f"status={resp.status_code} (token 是否过期? 中止本批)"
                )
                break
            if resp.status_code >= 500:
                logger.warning(
                    f"longbridge_api.fetch 5xx symbol={symbol} "
                    f"status={resp.status_code}"
                )
                continue
            if resp.status_code >= 400:
                logger.warning(
                    f"longbridge_api.fetch 4xx symbol={symbol} "
                    f"status={resp.status_code}"
                )
                continue
            try:
                payload = resp.json()
            except ValueError as e:
                logger.warning(
                    f"longbridge_api.json_parse_failed symbol={symbol}: {e}"
                )
                continue
            for art in parse_longbridge_news_json(payload, symbol=symbol):
                if art.original_url in seen_urls:
                    continue
                seen_urls.add(art.original_url)
                out.append(art)
        except (TimeoutError, httpx.HTTPError) as e:
            logger.warning(
                f"longbridge_api.fetch_failed symbol={symbol}: "
                f"{type(e).__name__}: {e}"
            )
            continue
        except Exception as e:  # noqa: BLE001
            logger.exception(
                f"longbridge_api.fetch_unexpected symbol={symbol}: {e}"
            )
            continue

    return out


class LongbridgeApiClient:
    """``ArticleSource`` 实现: 长桥证券 OpenAPI 港股新闻.

    与 :class:`SogouWechatClient` 不同, 长桥是**官方授权接口** (非爬虫),
    走 token 鉴权 + JSON 响应, 0 反爬维护成本.

    构造时接 ``symbols: list[str]`` (HK 代码列表, 例 ``["00700.HK", "01187.HK"]``);
    dispatcher 从活跃 IPO 索引拿当前在申/即将上市的 HK code.

    **未配置 token 时**: ``settings.longbridge_api_token`` 为空 → ``fetch()`` 直接
    返 []. dispatcher 注册时也会跳过, 不会浪费一个 source 槽位.

    版权:
    - 长桥 OpenAPI 是官方授权, 数据使用合规
    - ``source_credibility = 3`` 等同持牌媒体
    - ``is_full_text_available = True`` 长桥 link 允许 webview 渲染
    - ``source_name = "长桥·<原 source>"`` 显式标记数据来源
    """

    name: str = "长桥 OpenAPI"

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
        """token 配了才启用; 没配返 False, dispatcher 据此决定要不要注册."""
        return bool(self._settings.longbridge_api_token)

    async def fetch(self, *, since: datetime | None = None) -> list[ArticleRaw]:
        if not self.is_enabled:
            logger.debug("longbridge_api.skipped — token 未配置")
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
            return await fetch_longbridge_with_client(
                client,
                symbols=symbols,
                base_url=s.longbridge_api_base_url,
                news_path=s.longbridge_api_news_path,
                semaphore=sem,
                request_timeout=s.article_ingest_request_timeout_seconds,
                inter_query_delay_seconds=s.longbridge_api_inter_query_delay_seconds,
            )


__all__ = [
    "DEFAULT_LONGBRIDGE_BASE_URL",
    "LONGBRIDGE_NEWS_PATH",
    "LONGBRIDGE_COMMUNITY_PATH",
    "LONGBRIDGE_SOURCE_PREFIX",
    "LongbridgeApiClient",
    "fetch_longbridge_with_client",
    "parse_longbridge_news_json",
]
