"""财联社全球财经文章源 (BUG-S8-001 / spec/23 §C 类 ⏳ 推荐).

数据源选型记录
==============
Sprint 7.3 ext (spec/23) spike 28 源后 Top 4 推荐之一. 用户 bug-fix-23:31 提出 14 个
新思路, 实地 spike 发现 AKShare v1.18 的 ``stock_info_global_cls`` 接口直接封装财联社
全球财经资讯 (cls.cn 持牌媒体), pip install + 一行代码即可接入, 0 反爬维护.

财联社 (财经联合社) 是同花顺旗下持牌财经媒体, 港股/A股/美股/全球财经快讯主战场,
覆盖度堪比智通财经, 与智通 RSS 形成**维度互补** (spec/23 §维度互补).

API 调用
========
``df = ak.stock_info_global_cls(symbol='全部')``

参数:
- ``symbol``: ``'全部'`` 全部分类 / ``'港股'`` 港股 / ``'A股'`` / ``'美股'`` 等

返回 DataFrame, 字段 (实地 spike v1.18.57 确认):
- ``标题``: 文章标题, **可能为空字符串** (财联社快讯纯内容形式)
- ``内容``: 文章正文 (财联社快讯通常是短文 100-500 字)
- ``发布日期``: ``datetime.date``
- ``发布时间``: ``datetime.time``

字段映射
========
- 标题空 → 内容首句 (≤ 30 字) 作 title fallback
- 标题 + 内容 → ``ArticleRaw.title`` / ``ArticleRaw.summary`` (内容截断 ≤ 200 字)
- 发布日期 + 发布时间 → 拼成 ``datetime`` (CST UTC+8 → UTC)
- ``original_url``: 财联社接口**不返 url**, 用 ``cls.cn/detail/<hash>`` 占位 (基于
  内容 hash, 保证 ON CONFLICT (original_url) UNIQUE 约束)
- ``source_name = "财联社·<symbol 分类>"`` (例 ``财联社·全部``)
- ``market = "BOTH"`` (财联社快讯跨市场, dispatcher 走 IPOKeywordIndex 反查后期补)
- ``source_credibility = 3`` 持牌媒体高公信力
- ``is_full_text_available = True`` 内容已全文随接口返回

测试性
======
- :func:`parse_cls_dataframe` 纯函数 (``DataFrame -> list[ArticleRaw]``), fixture
  直接喂 mock DF
- :func:`fetch_cls_with_runner` 接外部 ``runner`` callable (默认 ``ak.stock_info_global_cls``),
  让单测注入 mock runner 不实跑 akshare
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.services.article_ingest.sources.base import ArticleRaw

# 数据源前缀 — FE 按 source_name.startsWith("财联社·") filter 可分流
CLS_SOURCE_PREFIX = "财联社·"

# original_url 占位前缀 (akshare 不返 url)
CLS_FAKE_URL_PREFIX = "https://cls.cn/detail/"

# 中国时区 (财联社发布时间是北京时间 UTC+8)
_CST = timezone(timedelta(hours=8))

# 默认 runner — 单测可注入 mock 替代
_DEFAULT_RUNNER: Callable[..., Any] | None = None


def _make_fake_url(content: str, dt: datetime) -> str:
    """基于 content + dt 生成稳定 hash URL (DB UNIQUE 约束需要).

    财联社快讯接口不返 link, 但同一条快讯每次抓取 content + dt 不变, hash 稳定,
    走 ON CONFLICT (original_url) DO NOTHING 即天然幂等.
    """
    h = hashlib.sha256(
        f"{content}|{dt.isoformat()}".encode("utf-8", errors="ignore")
    ).hexdigest()[:16]
    return f"{CLS_FAKE_URL_PREFIX}{h}"


def _truncate(s: str, n: int) -> str:
    """截断长度并去多空白."""
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _join_date_time_to_utc(d: Any, t: Any) -> datetime | None:
    """``date`` + ``time`` → datetime UTC (CST → UTC).

    akshare 返回的 d/t 是 ``datetime.date`` / ``datetime.time``; 也兼容字符串.
    """
    try:
        if isinstance(d, str):
            d = date.fromisoformat(d.split(" ")[0])
        if isinstance(t, str):
            t = time.fromisoformat(t.split(".")[0])
        if not isinstance(d, date) or not isinstance(t, time):
            return None
        local = datetime.combine(d, t, tzinfo=_CST)
        return local.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def parse_cls_dataframe(
    df: Any, *, symbol: str = "全部"
) -> list[ArticleRaw]:
    """财联社 DataFrame → ``list[ArticleRaw]``.

    单条解析失败 → ``logger.debug`` skip; 不抛.
    """
    out: list[ArticleRaw] = []
    if df is None:
        return out
    try:
        rows = df.to_dict("records") if hasattr(df, "to_dict") else list(df)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"cls_global.df_to_dict_failed symbol={symbol}: {e}")
        return out

    seen_urls: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            title_raw = (row.get("标题") or "").strip()
            content_raw = (row.get("内容") or "").strip()
            if not content_raw:
                continue  # 没正文 skip (财联社至少有 内容)

            # 标题空 → 用内容首句 (取 30 字) 作 title fallback
            title = title_raw or _truncate(content_raw, 30)
            if not title:
                continue

            published_at = _join_date_time_to_utc(
                row.get("发布日期"), row.get("发布时间")
            )
            if not published_at:
                continue

            url = _make_fake_url(content_raw, published_at)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            summary = _truncate(content_raw, 200) if content_raw else None

            out.append(
                ArticleRaw(
                    title=_truncate(title, 100),
                    original_url=url,
                    source_name=f"{CLS_SOURCE_PREFIX}{symbol}",
                    published_at=published_at,
                    summary=summary,
                    market="BOTH",
                    source_credibility=3,
                    is_full_text_available=True,
                )
            )
        except Exception as e:  # noqa: BLE001 — 单条 fail-soft
            logger.debug(f"cls_global.parse_row_failed symbol={symbol}: {e}")
            continue
    return out


# ─── 调用层 ─────────────────────────────────────────────────────────


def _resolve_runner() -> Callable[..., Any]:
    """import akshare 并返回 ``stock_info_global_cls`` runner.

    延迟 import — 避免 module load 期就 import 重磅库, 单测可在 ``_DEFAULT_RUNNER``
    上 monkeypatch 注入 mock.
    """
    if _DEFAULT_RUNNER is not None:
        return _DEFAULT_RUNNER
    import akshare as ak  # noqa: PLC0415 — lazy import

    runner: Callable[..., Any] = ak.stock_info_global_cls
    return runner


async def fetch_cls_with_runner(
    *,
    symbols: list[str],
    runner: Callable[..., Any] | None = None,
    inter_query_delay_seconds: float = 0.5,
) -> list[ArticleRaw]:
    """跑 N 个 symbol 类别拉财联社快讯, 合并去重.

    akshare 是同步函数, 用 ``asyncio.to_thread`` 包外让其在线程池跑, 不阻塞 event
    loop. 单 symbol 失败 → logger.warning skip + 继续. 去重: 按 original_url (hash).
    """
    runner = runner or _resolve_runner()
    all_articles: list[ArticleRaw] = []
    seen_urls: set[str] = set()

    for idx, symbol in enumerate(symbols):
        if not symbol:
            continue
        if idx > 0 and inter_query_delay_seconds > 0:
            await asyncio.sleep(inter_query_delay_seconds)
        try:
            df = await asyncio.to_thread(runner, symbol=symbol)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"cls_global.fetch_failed symbol={symbol}: "
                f"{type(e).__name__}: {e}"
            )
            continue
        for art in parse_cls_dataframe(df, symbol=symbol):
            if art.original_url in seen_urls:
                continue
            seen_urls.add(art.original_url)
            all_articles.append(art)

    return all_articles


class ClsGlobalClient:
    """``ArticleSource`` 实现: 财联社全球财经资讯 (akshare 封装).

    跑 ``ak.stock_info_global_cls(symbol)`` 拉财联社 cls.cn 实时快讯; 持牌财经
    媒体, 港 / A / 美 / 全球财经快讯全覆盖, 与智通 RSS 维度互补.

    版权:
    - akshare 是开源库, 内部走 cls.cn 公开接口
    - ``source_credibility = 3`` 持牌媒体高公信力
    - ``is_full_text_available = True`` 财联社快讯短文, 内容随接口全文返回, 不需
      跳外链查全文 (与智通 RSS 仅摘要不同)
    - ``source_name = "财联社·<symbol>"`` FE 可按前缀分流
    """

    name: str = "财联社·akshare"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        symbols: list[str] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        # 默认 ``全部`` — 用户可通过 ``article_ingest_cls_symbols`` 配置覆盖
        if symbols is None:
            cfg = self._settings.article_ingest_cls_symbols
            symbols = [s.strip() for s in cfg.split(",") if s.strip()]
        self._symbols = symbols

    async def fetch(self, *, since: datetime | None = None) -> list[ArticleRaw]:
        if not self._symbols:
            return []
        s = self._settings
        return await fetch_cls_with_runner(
            symbols=self._symbols,
            inter_query_delay_seconds=s.article_ingest_cls_inter_query_delay_seconds,
        )


__all__ = [
    "CLS_FAKE_URL_PREFIX",
    "CLS_SOURCE_PREFIX",
    "ClsGlobalClient",
    "fetch_cls_with_runner",
    "parse_cls_dataframe",
]
