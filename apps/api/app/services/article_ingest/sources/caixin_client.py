"""财新网文章源 (BUG-S8-002 / spec/23 §C 类 ⏳ 推荐).

数据源选型记录
==============
Sprint 7.3 ext (spec/23) Top 4 推荐之一. 财新 (Caixin) 是中国最权威持牌财经媒体之一,
覆盖财经/经济/政策/产业新闻, 与智通 RSS / 财联社快讯形成**3 源持牌媒体维度互补**.

实地 spike (akshare v1.18.57) 发现 ``stock_news_main_cx`` 接口直接返回财新主要
新闻 100 条, 字段 ``tag / summary / url``, 数据干净, 0 反爬维护成本.

API 调用
========
``df = ak.stock_news_main_cx()``  (无参数)

返回 DataFrame, 字段 (实地 spike 确认):
- ``tag``: 文章分类标签 (例 ``风电观察`` / ``华尔街原声`` / ``宏观``)
- ``summary``: 摘要 (≤ 200 字)
- ``url``: 文章链接 (例 ``https://database.caixin.com/2026-04-29/xxx.html``)

单次返回 100 条最近新闻.

字段映射
========
- ``url`` → ``ArticleRaw.original_url``  (财新真实 URL, 与 cls 不同, 不需 hash)
- ``summary`` 截首句 (≤ 30 字) → ``ArticleRaw.title``  (财新接口**没有 title**, 这是
  **本 client 最大字段挑战**)
- ``summary`` 完整 → ``ArticleRaw.summary``
- ``source_name = "财新·<tag>"``  (例 ``财新·风电观察``)
- ``published_at``: 财新接口**没有 time 字段**, 用 ``datetime.now(UTC)`` 兜底标
  ingest 时间 (财新这个接口本身就是滚动列表, 抓时即近发布时刻; 与雪球 / 智通 RSS 同
  风格)
- ``market = "BOTH"``  (财新跨市场, dispatcher 走 IPOKeywordIndex 反查后期补)
- ``source_credibility = 3`` 持牌媒体
- ``is_full_text_available = False``  (摘要返回, 全文需点 url 跳外链)

测试性
======
- :func:`parse_caixin_dataframe` 纯函数 (DataFrame -> list[ArticleRaw])
- :func:`fetch_caixin_with_runner` 接外部 ``runner`` callable, 单测注入 mock
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.services.article_ingest.sources.base import ArticleRaw

# 数据源前缀
CAIXIN_SOURCE_PREFIX = "财新·"


def _truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def parse_caixin_dataframe(df: Any) -> list[ArticleRaw]:
    """财新网 DataFrame → ``list[ArticleRaw]``.

    单条解析失败 → ``logger.debug`` skip; 不抛.
    """
    out: list[ArticleRaw] = []
    if df is None:
        return out
    try:
        rows = df.to_dict("records") if hasattr(df, "to_dict") else list(df)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"caixin.df_to_dict_failed: {e}")
        return out

    seen_urls: set[str] = set()
    now_utc = datetime.now(UTC)
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            url = (row.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            summary_full = (row.get("summary") or "").strip()
            if not summary_full:
                continue
            tag = (row.get("tag") or "财经").strip() or "财经"
            seen_urls.add(url)

            # 财新接口没有 title → summary 首句作 title (≤ 30 字)
            title = _truncate(summary_full, 30)

            out.append(
                ArticleRaw(
                    title=title,
                    original_url=url,
                    source_name=f"{CAIXIN_SOURCE_PREFIX}{tag}",
                    published_at=now_utc,  # 财新接口无时间字段, 用 now 兜底
                    summary=_truncate(summary_full, 200),
                    market="BOTH",
                    source_credibility=3,
                    is_full_text_available=False,
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"caixin.parse_row_failed: {e}")
            continue
    return out


# ─── 调用层 ─────────────────────────────────────────────────────────


def _resolve_runner() -> Callable[..., Any]:
    """延迟 import akshare, 返回 ``stock_news_main_cx`` runner."""
    import akshare as ak  # noqa: PLC0415 — lazy import

    runner: Callable[..., Any] = ak.stock_news_main_cx
    return runner


async def fetch_caixin_with_runner(
    *,
    runner: Callable[..., Any] | None = None,
) -> list[ArticleRaw]:
    """跑 ``stock_news_main_cx`` 拉财新主要新闻 100 条, 解析返回.

    akshare 是同步函数, 用 ``asyncio.to_thread`` 包外不阻塞 event loop.
    失败 → logger.warning skip + 返 [].
    """
    runner = runner or _resolve_runner()
    try:
        df = await asyncio.to_thread(runner)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"caixin.fetch_failed: {type(e).__name__}: {e}")
        return []
    return parse_caixin_dataframe(df)


class CaixinClient:
    """``ArticleSource`` 实现: 财新网主要新闻 (akshare 封装).

    跑 ``ak.stock_news_main_cx()`` 拉财新 100 条最近新闻; 中国最权威持牌财经媒体
    之一, 覆盖财经 / 经济 / 政策 / 产业 / 全球, 与智通 RSS / 财联社快讯形成 3 源
    持牌媒体维度互补.

    版权:
    - akshare 是开源库, 内部走财新公开接口
    - ``source_credibility = 3`` 持牌媒体
    - ``is_full_text_available = False`` 摘要返回, 全文跳外链 (与智通 RSS 同款)
    - ``source_name = "财新·<tag>"`` FE 可按前缀分流
    """

    name: str = "财新·akshare"

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def fetch(self, *, since: datetime | None = None) -> list[ArticleRaw]:
        return await fetch_caixin_with_runner()


__all__ = [
    "CAIXIN_SOURCE_PREFIX",
    "CaixinClient",
    "fetch_caixin_with_runner",
    "parse_caixin_dataframe",
]
