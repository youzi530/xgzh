"""搜狗微信公众号搜索 文章源 (BUG-S6.9-001 / spec/18 §Spike).

数据源选型记录
==============
Sprint 6.7 / 6.8 已经接了 4 源 (xueqiu / zhitong RSS / sina roll / em-search),
覆盖了**持牌媒体**与**雪球长文社区**, 但缺**微信公众号大V** 维度的文章 — 这是
港股新股投资者 (尤其是散户) 实际信息源最大头.

用户在 ``bug-fix-18:53`` 重新提出"大V点评 tab", 去掉了 6.8 spike 试过的"新榜/
清博"等付费平台名, 让本轮重新 spike. 6.9 spike 6 个候选源唯一 200 OK 的是:

    https://weixin.sogou.com/weixin?type=2&query=<IPO 名>

搜狗是腾讯收购前与微信的官方合作搜索方, 搜出来的是**微信公众号正文**(经搜狗
代理一道), 30kb 实体 + 0 反爬触发 + 10 条结构化文章 / 关键词. 目前免费 + 公开,
spike 4 个 IPO × 10 条 = **40 条公众号文章**, 大V 库覆盖 (证券日报之声 / 珍兴
资本 / Kai的费曼学习 / 港股新动力 / 雷递 / 龙龟新鉴 / 浊浪淘沙 等数十个).

用户提的 4 个 KOL (每天打个新 / 新股资本 / 财哥看十年 / 我爱广州GZ) 按 IPO 名
search **0 命中** — 因为他们标题习惯写 "今日打新 X 月 X 日" 通用名, 不直接含
IPO 名. 反过来按 KOL 名 search 倒能命中 (《【港股IPO】可孚医疗申购情况及打新
分析》 from 每天打个新), 但 4 KOL × N IPO = 4N 次请求, 反爬风险上升, 用户拍板
``hybrid + all_open`` — 不锁 KOL 白名单, 单 IPO 1 次 search, 搜狗返什么 KOL
都收, FE 按 ``source_name`` 前缀 ``"微信·"`` 过滤大V tab.

API endpoint
============
``GET https://weixin.sogou.com/weixin?type=2&query=<keyword>``

::

    type=2 文章搜索 (type=1 公众号搜索, spike 0 命中, 不用)
    query=<urlencoded 中文>

响应是 HTML, ``li[id^=sogou_vr_11002601_box_]`` 选 10 条文章卡片:

::

    <li id="sogou_vr_11002601_box_0">
      <div class="img-box"><a href="/link?url=..."><img src="..."/></a></div>
      <div class="txt-box">
        <h3>
          <a href="/link?url=...&type=2&query=...&token=..." uigs="article_title_0">
            <em><!--red_beg-->可孚医疗<!--red_end--></em>:"智造"健康管家
          </a>
        </h3>
        <p class="txt-info">可孚医疗货品周转与客户响应效率大幅提升...</p>
        <div class="s-p">
          <span class="all-time-y2">证券日报之声</span>
          <span class="s2"><script>document.write(timeConvert('1774022099'))</script></span>
        </div>
      </div>
    </li>

字段映射
========
- ``h3 a`` text (去 ``<em>`` 高亮 + ``<!--red_*-->`` 注释) → ``ArticleRaw.title``
- ``h3 a[href]`` 拼前缀 → ``original_url`` (``/link?url=...&token=...`` 是搜狗
  代理跳转链, FE webview 直接打开会自动跳到 mp.weixin.qq.com 真文章页, 后端
  不需要 follow 第二跳)
- ``.s-p .all-time-y2`` text → 公众号名字 → 包前缀 ``"微信·"`` → ``source_name``
  (FE 按 ``startsWith("微信·")`` filter 大V类 vs 持牌媒体)
- ``.s-p .s2 script`` 里 ``timeConvert('1774022099')`` regex 抽 unix 秒 →
  ``published_at`` UTC. 不需要执行 JS
- ``.txt-info`` text → ``summary``
- ``market = "BOTH"`` (搜狗按 IPO 名搜文章, 港 A 都有, dispatcher 的
  ``IPOKeywordIndex`` 后续会重新匹配 IPO market)
- ``source_credibility = 2`` (公众号大V 中等公信力, 比持牌媒体低 1, 但比无源
  转载站高; 可由运营在 DB 单条调整)
- ``is_full_text_available = False`` — 搜狗代理跳转的 mp.weixin 文章页不允许
  跨站 inline 渲染 (CORS + 鉴权 + 防盗链), FE 强制跳外链浏览全文

反爬 & 节流
==========
spike 阶段 0 反爬, 但搜狗历史上对**高频访问**有"请输入验证码" / 直接重定向到
``/antispider`` 的反爬模式. 防御措施:

1. **fail-soft**: parse 阶段先检测 ``"antispider"`` / ``"请输入验证码"`` 关键字,
   命中即返 [] + ``logger.warning``, 不抛
2. **节流**: ``register_sources`` 里只给 ``article_ingest_sogou_max_queries`` (默认
   10) 个 IPO query, 不全跑; sem 限并发 = ``article_ingest_request_concurrency``
3. **缓存兜底**: dispatcher 上层走 PG ``ON CONFLICT (original_url) DO NOTHING``,
   重复抓到同一 URL 直接 skip; 即使触发反爬, 已有数据不丢

如果未来反爬升级到必须破 token, 切到付费源 (新榜 API) 是 fallback.

测试性
======
- :func:`parse_sogou_html` 纯函数 (``str -> list[ArticleRaw]``), fixture 直接
  喂 HTML 字符串
- :func:`fetch_sogou_with_client` 接外部 ``httpx.AsyncClient``, 让 ``respx_mock``
  注入测试 client (复用 EM-search / Sina 同款方式)
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup, Tag

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.services.article_ingest.sources.base import ArticleRaw

SOGOU_WECHAT_URL = "https://weixin.sogou.com/weixin"
SOGOU_BASE_URL = "https://weixin.sogou.com"

# 公众号名字前缀 — FE 按 ``source_name.startsWith("微信·")`` filter 大V tab
WECHAT_SOURCE_PREFIX = "微信·"

# 反爬关键字: 命中即认为本次 search 被搜狗拦截, fail-soft 返空
_ANTISPIDER_KEYWORDS = ("antispider", "请输入验证码", "weixin.sogou.com/antispider")

# 时间戳 regex: 从 ``timeConvert('1774022099')`` 抽 unix 秒
_TIMECONVERT_RE = re.compile(r"timeConvert\(\s*['\"]?(\d{10})['\"]?\s*\)")

# ``<em>`` 高亮 tag + ``<!--red_beg-->`` / ``<!--red_end-->`` 注释 (搜狗用它包关键词)
_EM_TAG_RE = re.compile(r"</?em[^>]*>", re.I)
_RED_COMMENT_RE = re.compile(r"<!--\s*red_(beg|end)\s*-->", re.I)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_highlight(s: str | None) -> str | None:
    """去 ``<em>`` + ``<!--red_*-->`` + 多空白; 返 None 则视为脏数据."""
    if not s:
        return None
    cleaned = _RED_COMMENT_RE.sub("", s)
    cleaned = _EM_TAG_RE.sub("", cleaned)
    cleaned = _HTML_TAG_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _parse_timestamp(script_html: str) -> datetime | None:
    """``<script>document.write(timeConvert('1774022099'))</script>`` → datetime UTC.

    搜狗 HTML 里时间字段是 JS ``timeConvert(unix_seconds)`` 待执行表达式;
    后端不执行 JS, 直接 regex 抽数字. 解析失败 → None (调用方 skip).
    """
    if not script_html:
        return None
    m = _TIMECONVERT_RE.search(script_html)
    if not m:
        return None
    try:
        n = int(m.group(1))
        # 防御: 1980-01-01 (315532800) ≤ ts ≤ 2100-01-01 (4102444800)
        if not 315532800 <= n <= 4102444800:
            return None
        return datetime.fromtimestamp(n, tz=UTC)
    except (TypeError, ValueError):
        return None


def _is_antispider(html: str) -> bool:
    """检测搜狗反爬 HTML; 命中即认为本次 search 被拦截."""
    if not html:
        return True
    lower = html.lower()
    return any(kw.lower() in lower for kw in _ANTISPIDER_KEYWORDS)


def _absolutize_url(href: str | None) -> str | None:
    """搜狗的 ``/link?url=...`` 是相对路径, 拼 base 成绝对 URL.

    保留 ``/link?url=...&token=...`` 不 follow 第二跳 — FE webview 打开时搜狗
    会自动跳到真正的 mp.weixin.qq.com 文章页, 后端 follow 一次还会跳一次,
    成本 + 反爬风险 + 拿不到稳定 URL 三重失. 直接保留搜狗代理 URL.
    """
    if not href:
        return None
    href = href.strip()
    if not href:
        return None
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return SOGOU_BASE_URL + href
    return None


def parse_sogou_html(html: str, *, query: str | None = None) -> list[ArticleRaw]:
    """搜狗微信 search HTML → ``list[ArticleRaw]``.

    单条解析失败 → ``logger.debug`` skip (与其它 source fail-soft 一致).
    整页反爬命中 → 整批返 [] + ``logger.warning`` (不抛).

    ``query`` 仅用于日志, 不影响解析逻辑.
    """
    if not html or _is_antispider(html):
        if html:
            logger.warning(
                f"sogou_wechat.antispider_triggered query={query!r} (返空, 不抛)"
            )
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:  # noqa: BLE001 — html5 lib 不在依赖, BS4 罕见也可能 raise
        logger.warning(f"sogou_wechat.bs4_parse_failed query={query!r}: {e}")
        return []

    items = soup.select("li[id^=sogou_vr_11002601_box_]")
    if not items:
        # 搜狗有时换 selector — 兜底取 .news-list2 li
        items = soup.select("ul.news-list2 > li") or soup.select("ul.news-list > li")

    out: list[ArticleRaw] = []
    for item in items:
        if not isinstance(item, Tag):
            continue
        try:
            title_a = item.select_one("h3 a")
            if title_a is None:
                continue
            title = _strip_highlight(title_a.decode_contents())
            if not title:
                continue

            href_raw = title_a.get("href")
            href: str | None = href_raw if isinstance(href_raw, str) else None
            url = _absolutize_url(href)
            if not url:
                continue

            nick_el = item.select_one(".s-p .all-time-y2") or item.select_one(
                ".all-time-y2"
            )
            kol_name = nick_el.get_text(strip=True) if nick_el else ""
            if not kol_name:
                # 没公众号名 → 没法判定大V类, skip
                continue
            source_name = f"{WECHAT_SOURCE_PREFIX}{kol_name}"

            time_script = item.select_one(".s-p .s2") or item.select_one(".s2")
            time_html = time_script.decode_contents() if time_script else ""
            published_at = _parse_timestamp(time_html)
            if not published_at:
                # 没时间戳 → DB published_at NOT NULL, skip
                continue

            summary_el = item.select_one(".txt-info") or item.select_one(
                "[id^=sogou_vr_11002601_summary_]"
            )
            summary = (
                _strip_highlight(summary_el.decode_contents()) if summary_el else None
            )

            out.append(
                ArticleRaw(
                    title=title,
                    original_url=url,
                    source_name=source_name,
                    published_at=published_at,
                    summary=summary,
                    market="BOTH",
                    source_credibility=2,
                    is_full_text_available=False,
                )
            )
        except Exception as e:  # noqa: BLE001 — 单条 fail-soft
            logger.debug(f"sogou_wechat.parse_item_failed query={query!r}: {e}")
            continue
    return out


# ─── HTTP layer ─────────────────────────────────────────────────────


async def fetch_sogou_with_client(
    client: httpx.AsyncClient,
    *,
    queries: list[str],
    semaphore: asyncio.Semaphore | None = None,
    request_timeout: float = 10.0,
    inter_query_delay_seconds: float = 1.5,
) -> list[ArticleRaw]:
    """走外部 ``httpx.AsyncClient`` 跑 N 个 IPO 关键词搜索, 合并去重.

    每个 query 是 IPO 名 (``可孚医疗`` / ``天星医疗``); ``Semaphore`` 限并发,
    ``inter_query_delay_seconds`` 在每个 query 之间 ``asyncio.sleep`` 节流,
    防止搜狗按 IP 快速访问触发反爬. spike 期间观察到连续 5 次 / 10s 内必触发,
    1.5s 间隔下连续 10 query 实测稳定. 单 query 失败 → ``logger.warning``
    skip + 继续. 去重: 按 ``original_url`` 取首条.

    设置 ``inter_query_delay_seconds=0`` 跑测试, 让 respx 注入立即返回.
    """
    sem = semaphore or asyncio.Semaphore(2)
    seen_urls: set[str] = set()
    out: list[ArticleRaw] = []

    for idx, q in enumerate(queries):
        if not q:
            continue
        # 节流: 第 2+ 个 query 之前等 N 秒. 第 1 个 query 不等 (整体提速).
        if idx > 0 and inter_query_delay_seconds > 0:
            await asyncio.sleep(inter_query_delay_seconds)
        try:
            async with sem:
                resp = await client.get(
                    SOGOU_WECHAT_URL,
                    params={"type": 2, "query": q},
                    timeout=request_timeout,
                )
            if resp.status_code >= 500:
                logger.warning(
                    f"sogou_wechat.fetch 5xx q={q} status={resp.status_code}"
                )
                continue
            if resp.status_code >= 400:
                logger.warning(
                    f"sogou_wechat.fetch 4xx q={q} status={resp.status_code}"
                )
                continue
            for art in parse_sogou_html(resp.text, query=q):
                if art.original_url in seen_urls:
                    continue
                seen_urls.add(art.original_url)
                out.append(art)
        except (TimeoutError, httpx.HTTPError) as e:
            logger.warning(
                f"sogou_wechat.fetch_failed q={q}: {type(e).__name__}: {e}"
            )
            continue
        except Exception as e:  # noqa: BLE001
            logger.exception(f"sogou_wechat.fetch_unexpected q={q}: {e}")
            continue

    return out


class SogouWechatClient:
    """``ArticleSource`` 实现: 搜狗微信公众号搜索.

    与 :class:`XueqiuClient` / :class:`EastmoneySearchClient` 一样关键词驱动 —
    构造时接 ``queries: list[str]``, dispatcher 从 :class:`IPOKeywordIndex` 拿
    每只活跃 IPO 的关键词. 单 IPO 1 次 HTTP, 反爬门槛低.

    版权:
    - 搜狗 ``/link?url=...`` 是搜狗代理跳转链, FE webview 打开时会被搜狗自动跳
      到 mp.weixin.qq.com 真文章页, 等同点击搜狗搜索结果, 合规.
    - ``is_full_text_available = False``: 不允许 inline 渲染微信公众号正文
      (CORS + 防盗链), FE 强制跳外链浏览全文.
    - ``source_name = "微信·<公众号名>"``: 显式标记数据源是公众号大V, FE 按
      ``startsWith("微信·")`` filter 出大V点评 tab.
    """

    name: str = "搜狗微信"

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
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://weixin.sogou.com/",
        }
        sem = asyncio.Semaphore(s.article_ingest_request_concurrency)
        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=s.article_ingest_request_timeout_seconds,
        ) as client:
            return await fetch_sogou_with_client(
                client,
                queries=self._queries,
                semaphore=sem,
                request_timeout=s.article_ingest_request_timeout_seconds,
                inter_query_delay_seconds=s.article_ingest_sogou_inter_query_delay_seconds,
            )


__all__ = [
    "SOGOU_WECHAT_URL",
    "WECHAT_SOURCE_PREFIX",
    "SogouWechatClient",
    "fetch_sogou_with_client",
    "parse_sogou_html",
]
