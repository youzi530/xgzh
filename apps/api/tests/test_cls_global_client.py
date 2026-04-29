"""BUG-S8-001: ``cls_global_client`` 单元测试.

覆盖:

A. ``parse_cls_dataframe`` 纯函数:
    1. happy 3 条 (来自 ak.stock_info_global_cls 真实字段) → 3 条 ArticleRaw
    2. 标题为空 → 内容首句作 title fallback (≤30 字)
    3. 缺内容 → skip 单条
    4. 同一内容+时间重复 → 去重 (hash 稳定)
    5. None / 空 DF → []
    6. 时间字段非 date/time 类型 → skip

B. ``fetch_cls_with_runner`` 调用层:
    7. mock runner 多 symbol 返 happy DF → 多 symbol 合并去重
    8. mock runner raise → 该 symbol skip + 其他继续

C. ``ClsGlobalClient`` 行为:
    9. 空 symbols → fetch 返 []
    10. 默认 symbols 来自 settings.article_ingest_cls_symbols ('全部')
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

import pandas as pd
import pytest

from app.core.config import Settings
from app.services.article_ingest.sources.cls_global_client import (
    CLS_FAKE_URL_PREFIX,
    CLS_SOURCE_PREFIX,
    ClsGlobalClient,
    fetch_cls_with_runner,
    parse_cls_dataframe,
)


def _make_settings(**overrides) -> Settings:
    base = {
        "article_ingest_cls_symbols": "全部",
        "article_ingest_cls_inter_query_delay_seconds": 0.0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ─── A. parse_cls_dataframe 纯函数 ─────────────────────────────────


def test_parse_happy_3_rows() -> None:
    df = pd.DataFrame(
        [
            {
                "标题": "*ST国化:收到拟终止公司股票上市的事先告知书",
                "内容": "财联社4月29日电,*ST国化(600636.SH)公告称...",
                "发布日期": date(2026, 4, 29),
                "发布时间": time(23, 14, 51),
            },
            {
                "标题": "",
                "内容": "财联社4月29日电,ICE原糖期货上涨逾3%,至每磅14.57美分.",
                "发布日期": date(2026, 4, 29),
                "发布时间": time(23, 11, 28),
            },
            {
                "标题": "交易员加大对英国央行的押注",
                "内容": "财联社4月29日电,交易员加大押注,预计2026年三次加息.",
                "发布日期": date(2026, 4, 29),
                "发布时间": time(23, 15, 12),
            },
        ]
    )
    out = parse_cls_dataframe(df, symbol="全部")
    assert len(out) == 3
    a = out[0]
    assert a.title.startswith("*ST国化")
    assert a.source_name == f"{CLS_SOURCE_PREFIX}全部"
    assert a.original_url.startswith(CLS_FAKE_URL_PREFIX)
    assert a.market == "BOTH"
    assert a.source_credibility == 3
    assert a.is_full_text_available is True
    # CST 23:14:51 → UTC 15:14:51 (UTC+8 转)
    expected_utc = datetime(2026, 4, 29, 23, 14, 51, tzinfo=timezone(timedelta(hours=8))).astimezone(UTC)
    assert a.published_at == expected_utc


def test_parse_empty_title_falls_back_to_content() -> None:
    df = pd.DataFrame(
        [
            {
                "标题": "",
                "内容": "财联社4月29日电,ICE原糖期货上涨逾3%,至每磅14.57美分.",
                "发布日期": date(2026, 4, 29),
                "发布时间": time(23, 11, 28),
            }
        ]
    )
    out = parse_cls_dataframe(df, symbol="全部")
    assert len(out) == 1
    # title 由内容首段截 30 字
    assert "财联社4月29日电" in out[0].title
    assert len(out[0].title) <= 30


def test_parse_skip_missing_content() -> None:
    df = pd.DataFrame(
        [
            {"标题": "ok", "内容": "", "发布日期": date(2026, 4, 29), "发布时间": time(10, 0)},
            {"标题": "", "内容": "", "发布日期": date(2026, 4, 29), "发布时间": time(10, 0)},
            {
                "标题": "kept",
                "内容": "real content",
                "发布日期": date(2026, 4, 29),
                "发布时间": time(10, 0),
            },
        ]
    )
    out = parse_cls_dataframe(df, symbol="全部")
    assert len(out) == 1
    assert out[0].title == "kept"


def test_parse_dedup_same_content_time() -> None:
    df = pd.DataFrame(
        [
            {
                "标题": "first",
                "内容": "same content",
                "发布日期": date(2026, 4, 29),
                "发布时间": time(10, 0),
            },
            {
                "标题": "dup",
                "内容": "same content",  # 与 first 同内容同时间 → hash 同 → skip
                "发布日期": date(2026, 4, 29),
                "发布时间": time(10, 0),
            },
        ]
    )
    out = parse_cls_dataframe(df, symbol="全部")
    assert len(out) == 1
    assert out[0].title == "first"


def test_parse_empty_or_none() -> None:
    assert parse_cls_dataframe(None) == []
    assert parse_cls_dataframe(pd.DataFrame(columns=["标题", "内容", "发布日期", "发布时间"])) == []


def test_parse_skip_invalid_date_time_type() -> None:
    df = pd.DataFrame(
        [
            {
                "标题": "ok",
                "内容": "content",
                "发布日期": "not-a-date",
                "发布时间": "not-a-time",
            }
        ]
    )
    out = parse_cls_dataframe(df, symbol="全部")
    assert out == []  # invalid datetime → skip


# ─── B. fetch_cls_with_runner 调用层 ───────────────────────────────


@pytest.mark.asyncio
async def test_fetch_with_mock_runner_dedup_across_symbols() -> None:
    df_all = pd.DataFrame(
        [
            {
                "标题": "art-A",
                "内容": "content A",
                "发布日期": date(2026, 4, 29),
                "发布时间": time(10, 0),
            },
            {
                "标题": "shared",
                "内容": "shared content",
                "发布日期": date(2026, 4, 29),
                "发布时间": time(10, 5),
            },
        ]
    )
    df_hk = pd.DataFrame(
        [
            {
                "标题": "art-HK",
                "内容": "content HK",
                "发布日期": date(2026, 4, 29),
                "发布时间": time(10, 10),
            },
            {
                "标题": "shared dup",
                "内容": "shared content",  # 跨 symbol 同 content+time → hash 同 → skip
                "发布日期": date(2026, 4, 29),
                "发布时间": time(10, 5),
            },
        ]
    )

    def mock_runner(symbol: str):
        if symbol == "全部":
            return df_all
        if symbol == "港股":
            return df_hk
        return pd.DataFrame()

    out = await fetch_cls_with_runner(
        symbols=["全部", "港股"],
        runner=mock_runner,
        inter_query_delay_seconds=0.0,
    )
    assert len(out) == 3
    titles = {a.title for a in out}
    assert "art-A" in titles
    assert "art-HK" in titles
    # shared 跨 symbol 仅保留 1 条
    urls = [a.original_url for a in out]
    assert len(set(urls)) == len(urls)


@pytest.mark.asyncio
async def test_fetch_runner_raises_one_symbol_skips() -> None:
    df_ok = pd.DataFrame(
        [
            {
                "标题": "ok",
                "内容": "content",
                "发布日期": date(2026, 4, 29),
                "发布时间": time(10, 0),
            }
        ]
    )

    def mock_runner(symbol: str):
        if symbol == "失败":
            raise RuntimeError("akshare 网络故障")
        return df_ok

    out = await fetch_cls_with_runner(
        symbols=["失败", "全部"],
        runner=mock_runner,
        inter_query_delay_seconds=0.0,
    )
    assert len(out) == 1
    assert out[0].title == "ok"


# ─── C. ClsGlobalClient 行为 ────────────────────────────────────


@pytest.mark.asyncio
async def test_client_empty_symbols_returns_empty() -> None:
    settings = _make_settings(article_ingest_cls_symbols="")
    c = ClsGlobalClient(settings=settings)
    out = await c.fetch()
    assert out == []


def test_client_default_symbols_from_config() -> None:
    settings = _make_settings(article_ingest_cls_symbols="全部,港股,A股")
    c = ClsGlobalClient(settings=settings)
    # 内部 symbols 应从 config 读 + 切割
    assert c._symbols == ["全部", "港股", "A股"]  # noqa: SLF001
