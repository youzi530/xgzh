"""东方财富搜索 API 文章源 (BUG-S6.7-005 / spec/16 §Spike #3).

数据源选型记录
==============
现有 4 源 (xueqiu / zhitong RSS / sina / em-search) 中, 东方财富 ``search-api-web``
是**财经全媒体聚合站** — 单次按 IPO 关键词搜, 一次能命中 10+ 篇来自 50+ 持牌媒体
(北京商报 / 证券时报 / 南财 / 界面 / 凤凰财经 / 财联社 / 第一财经 / ...) 的转载.
**接一个等于接 N 个**, 是市场文章覆盖度最高 ROI 的源.

API endpoint
============
``GET https://search-api-web.eastmoney.com/search/jsonp``

::

    ?cb=                     # JSONP callback (留空 = 不包 callback)
    &param=<urlencoded JSON>

``param`` 是 JSON object urlencoded:

::

    {
      "uid": "",
      "keyword": "可孚医疗",
      "type": ["cmsArticleWebOld"],   # cms = 全媒体文章
      "client": "web",
      "clientVersion": "curr",
      "pageSize": 10
    }

响应 (JSONP, 外层 ``({...});``):

::

    {
      "code": 0,
      "hitsTotal": 408,
      "result": {
        "cmsArticleWebOld": [
          {
            "date": "2026-04-29 16:43:57",
            "title": "<em>可孚医疗</em>一季度净利1.07亿元...",
            "content": "<em>可孚医疗</em>表示, 报告期内...",
            "mediaName": "北京商报",
            "url": "http://finance.eastmoney.com/a/202604293724502367.html"
          },
          ...
        ]
      }
    }

字段映射
========
- ``title`` (去 ``<em>`` 高亮 tag) → ``ArticleRaw.title``
- ``url`` → ``original_url`` (注: 是 eastmoney mirror, 真原始链接需点开 redirect)
- ``date`` (``"2026-04-29 16:43:57"``) → ``published_at``, 默认 Asia/Shanghai
- ``mediaName`` → ``source_name`` (动态, 反映原始媒体)
- ``content`` (去 ``<em>`` 高亮 tag) → ``summary``
- ``market = "BOTH"`` (全媒体涵盖港 A 美; dispatcher 关键词反查后细化)
- ``source_credibility = 3`` (持牌媒体, 高公信力)
- ``is_full_text_available = True`` (eastmoney mirror 页面允许浏览)

**dispatcher 启动时已经按 IPOKeywordIndex 把每只活跃 IPO 的关键词列表传进来,
本 source 同款 ``XueqiuClient`` 协议接收 ``queries: list[str]``, 每个 query 跑一次
搜索 API, 合并去重.**

测试性
======
- :func:`parse_eastmoney_search_response` 纯函数 (``dict -> list[ArticleRaw]``),
  fixture 直接喂 dict 测覆盖
- :func:`fetch_eastmoney_search_with_client` 接外部 ``httpx.AsyncClient``,
  让 ``respx_mock`` 注入测试 client
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.services.article_ingest.sources.base import ArticleRaw

EASTMONEY_SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"

# JSONP 回调的外层 ``(...);`` 或 ``foo({...});`` (cb 为空时也会有 paren).
# 第一个 group 可选 callback 名字 (空 cb → match group(1) 是空串).
_JSONP_OUTER_RE = re.compile(
    r"^\s*([a-zA-Z_$][\w$]*)?\s*\((.*)\)\s*;?\s*$", re.S
)

# 标题 / 摘要里 "<em>关键词</em>" 高亮 tag, 去掉只留纯文本.
_EM_TAG_PATTERN = re.compile(r"</?em[^>]*>", re.I)
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

# CST = UTC+8 (Asia/Shanghai); ``date`` 字段没带时区, 默认 CST.
_CST = timezone(timedelta(hours=8))


def _strip_em(text: str | None) -> str | None:
    """去 ``<em>`` 高亮 tag (东方财富搜索 API 用它包关键词)."""
    if not text:
        return None
    cleaned = _EM_TAG_PATTERN.sub("", text)
    cleaned = _HTML_TAG_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _parse_jsonp(text: str) -> dict[str, Any] | None:
    """JSONP 包装的 ``({...});`` → dict; 解包失败返 None.

    某些响应没 callback 包装, 直接是裸 JSON; 也兼容.
    """
    if not text:
        return None
    s = text.strip()
    m = _JSONP_OUTER_RE.match(s)
    body = m.group(2) if m else s
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"eastmoney_search.parse_jsonp_failed: {e}")
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _parse_published_at(raw: str | None) -> datetime | None:
    """``"2026-04-29 16:43:57"`` → datetime (CST tz-aware).

    解析失败 → None, 调用方 skip 整条 (DB ``published_at`` NOT NULL).
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(s, fmt)
            return naive.replace(tzinfo=_CST)
        except ValueError:
            continue
    return None


def parse_eastmoney_search_response(
    payload: dict[str, Any],
) -> list[ArticleRaw]:
    """``dict`` (JSONP 解包后) → ``list[ArticleRaw]``.

    单条解析失败 → ``logger.debug`` skip + 继续 (与雪球/智通 RSS fail-soft 一致).
    """
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    items = result.get("cmsArticleWebOld", [])
    if not isinstance(items, list):
        return []

    out: list[ArticleRaw] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            title = _strip_em(item.get("title"))
            if not title:
                continue
            url = (item.get("url") or "").strip()
            if not url:
                continue
            published_at = _parse_published_at(item.get("date"))
            if not published_at:
                # date 字段是必返的, 缺了视为脏数据 skip
                continue
            summary = _strip_em(item.get("content"))
            media_name = (item.get("mediaName") or "东方财富").strip() or "东方财富"

            out.append(
                ArticleRaw(
                    title=title,
                    original_url=url,
                    source_name=media_name,
                    published_at=published_at.astimezone(UTC),
                    summary=summary,
                    market="BOTH",
                    source_credibility=3,
                    is_full_text_available=True,
                )
            )
        except Exception as e:  # noqa: BLE001 — 单条 fail-soft
            logger.debug(f"eastmoney_search.parse_item_failed: {e}")
            continue
    return out


# ─── HTTP layer ─────────────────────────────────────────────────────


def _build_param(keyword: str, *, page_size: int = 10) -> str:
    """构造 ``param`` 查询参数: JSON 字符串.

    不要预先 ``quote`` — httpx ``params=`` 会自己做 URL encode, 预先 encode 会
    导致 double escape (``%7B`` → ``%257B``), 服务器返 400 "非法的 json 格式".
    """
    return json.dumps(
        {
            "uid": "",
            "keyword": keyword,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientVersion": "curr",
            "pageSize": page_size,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def fetch_eastmoney_search_with_client(
    client: httpx.AsyncClient,
    *,
    queries: list[str],
    page_size: int = 10,
    semaphore: asyncio.Semaphore | None = None,
    request_timeout: float = 10.0,
) -> list[ArticleRaw]:
    """走外部 ``httpx.AsyncClient`` 跑 N 条关键词搜索, 合并去重.

    每个 query 是 IPO 关键词 (``地平线机器人`` / ``可孚医疗``); ``Semaphore``
    限并发 (东方财富搜索 API 反爬不强, 但礼貌避免被封).
    单 query 失败 → warning + skip + 继续 (与 xueqiu 同款 fail-soft).
    去重: 按 ``original_url`` 取首条.
    """
    sem = semaphore or asyncio.Semaphore(2)
    seen_urls: set[str] = set()
    out: list[ArticleRaw] = []

    for q in queries:
        if not q:
            continue
        param = _build_param(q, page_size=page_size)
        try:
            async with sem:
                resp = await client.get(
                    EASTMONEY_SEARCH_URL,
                    params={"cb": "", "param": param},
                    timeout=request_timeout,
                )
            if resp.status_code >= 500:
                logger.warning(
                    f"eastmoney_search.fetch 5xx q={q} status={resp.status_code}"
                )
                continue
            if resp.status_code >= 400:
                logger.warning(
                    f"eastmoney_search.fetch 4xx q={q} status={resp.status_code}"
                )
                continue
            payload = _parse_jsonp(resp.text)
            if payload is None:
                continue
            for art in parse_eastmoney_search_response(payload):
                if art.original_url in seen_urls:
                    continue
                seen_urls.add(art.original_url)
                out.append(art)
        except (TimeoutError, httpx.HTTPError) as e:
            logger.warning(
                f"eastmoney_search.fetch_failed q={q}: {type(e).__name__}: {e}"
            )
            continue
        except Exception as e:  # noqa: BLE001
            logger.exception(f"eastmoney_search.fetch_unexpected q={q}: {e}")
            continue

    return out


class EastmoneySearchClient:
    """``ArticleSource`` 实现: 东方财富全媒体搜索 API.

    构造时接受 settings + 关键词列表 (与 :class:`XueqiuClient` 同款协议),
    一般由 dispatcher 从 :class:`IPOKeywordIndex` 拿每只活跃 IPO 的关键词.

    版权:
    - 实际跳转的 ``finance.eastmoney.com/a/...`` 是东方财富 mirror 页, 用户在
      站内查看时显示原始媒体名 (mediaName), 与雪球 status 同合规模型 — 显示
      标题 + 摘要 + 来源, 全文请跳外链.
    """

    name: str = "东方财富搜索"

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
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
            ),
            "Accept": "*/*",
            "Referer": "https://so.eastmoney.com/",
        }
        sem = asyncio.Semaphore(s.article_ingest_request_concurrency)
        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=s.article_ingest_request_timeout_seconds,
        ) as client:
            return await fetch_eastmoney_search_with_client(
                client,
                queries=self._queries,
                page_size=s.article_ingest_eastmoney_search_page_size,
                semaphore=sem,
                request_timeout=s.article_ingest_request_timeout_seconds,
            )


__all__ = [
    "EASTMONEY_SEARCH_URL",
    "EastmoneySearchClient",
    "fetch_eastmoney_search_with_client",
    "parse_eastmoney_search_response",
]
