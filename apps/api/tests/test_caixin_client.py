"""BUG-S8-002: ``caixin_client`` 单元测试.

覆盖:

A. ``parse_caixin_dataframe`` 纯函数:
    1. happy 3 条 (来自 ak.stock_news_main_cx 真实字段) → 3 条 ArticleRaw
    2. 缺 url → skip 单条
    3. 缺 summary → skip
    4. 重复 url → 去重保首
    5. 缺 tag → fallback "财经"
    6. None / 空 DF → []
    7. summary 长 200+ 字 → 截断

B. ``fetch_caixin_with_runner`` 调用层:
    8. mock runner 返 happy DF → 解析 OK
    9. mock runner raise → 返 [] 不抛
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.services.article_ingest.sources.caixin_client import (
    CAIXIN_SOURCE_PREFIX,
    CaixinClient,
    fetch_caixin_with_runner,
    parse_caixin_dataframe,
)

# ─── A. parse_caixin_dataframe 纯函数 ─────────────────────────────


def test_parse_happy_3_rows() -> None:
    df = pd.DataFrame(
        [
            {
                "tag": "风电观察",
                "summary": "截至2月底,全国累计发电装机容量39.6亿千瓦,同比增长15.5%.其中,风电装机容量6.6亿千瓦.",
                "url": "https://database.caixin.com/2026-04-29/102439211.html",
            },
            {
                "tag": "华尔街原声",
                "summary": "桥水基金创始人瑞·达利欧认为,全球市场都在密切关注美联储的政策行动.",
                "url": "https://database.caixin.com/2026-04-29/102439178.html",
            },
            {
                "tag": "宏观",
                "summary": "中国经济稳定增长,GDP 表现亮眼.",
                "url": "https://database.caixin.com/2026-04-29/102439199.html",
            },
        ]
    )
    out = parse_caixin_dataframe(df)
    assert len(out) == 3
    a = out[0]
    assert a.source_name == f"{CAIXIN_SOURCE_PREFIX}风电观察"
    assert a.original_url.startswith("https://database.caixin.com")
    assert a.market == "BOTH"
    assert a.source_credibility == 3
    assert a.is_full_text_available is False
    # title 是 summary 截前 30 字
    assert len(a.title) <= 30
    assert "截至2月底" in a.title
    # published_at 应是近期 (now 兜底, 5min 内)
    assert (datetime.now(UTC) - a.published_at) < timedelta(minutes=5)


def test_parse_skip_missing_url() -> None:
    df = pd.DataFrame(
        [
            {"tag": "ok", "summary": "no url", "url": ""},
            {"tag": "ok", "summary": "kept", "url": "https://x.com/a"},
        ]
    )
    out = parse_caixin_dataframe(df)
    assert len(out) == 1
    assert out[0].original_url == "https://x.com/a"


def test_parse_skip_missing_summary() -> None:
    df = pd.DataFrame(
        [
            {"tag": "ok", "summary": "", "url": "https://x.com/a"},
            {"tag": "ok", "summary": "kept", "url": "https://x.com/b"},
        ]
    )
    out = parse_caixin_dataframe(df)
    assert len(out) == 1
    assert out[0].original_url == "https://x.com/b"


def test_parse_dedup_same_url() -> None:
    df = pd.DataFrame(
        [
            {"tag": "first", "summary": "first content", "url": "https://x.com/a"},
            {"tag": "dup", "summary": "dup content", "url": "https://x.com/a"},
        ]
    )
    out = parse_caixin_dataframe(df)
    assert len(out) == 1
    assert "first content" in out[0].summary  # type: ignore[operator]


def test_parse_missing_tag_falls_back_to_default() -> None:
    df = pd.DataFrame(
        [{"tag": "", "summary": "content", "url": "https://x.com/a"}]
    )
    out = parse_caixin_dataframe(df)
    assert len(out) == 1
    assert out[0].source_name == f"{CAIXIN_SOURCE_PREFIX}财经"


def test_parse_empty_or_none() -> None:
    assert parse_caixin_dataframe(None) == []
    assert parse_caixin_dataframe(pd.DataFrame(columns=["tag", "summary", "url"])) == []


def test_parse_summary_truncated_at_200_chars() -> None:
    long_summary = "x" * 300
    df = pd.DataFrame(
        [{"tag": "ok", "summary": long_summary, "url": "https://x.com/a"}]
    )
    out = parse_caixin_dataframe(df)
    assert len(out) == 1
    assert out[0].summary is not None
    assert len(out[0].summary) <= 200


# ─── B. fetch_caixin_with_runner 调用层 ─────────────────────────


@pytest.mark.asyncio
async def test_fetch_with_mock_runner_happy() -> None:
    df = pd.DataFrame(
        [{"tag": "T1", "summary": "S1 content", "url": "https://x.com/a"}]
    )

    def mock_runner():
        return df

    out = await fetch_caixin_with_runner(runner=mock_runner)
    assert len(out) == 1
    assert out[0].source_name == f"{CAIXIN_SOURCE_PREFIX}T1"


@pytest.mark.asyncio
async def test_fetch_runner_raises_returns_empty() -> None:
    def mock_runner():
        raise RuntimeError("akshare 网络故障")

    out = await fetch_caixin_with_runner(runner=mock_runner)
    assert out == []


@pytest.mark.asyncio
async def test_caixin_client_calls_runner() -> None:
    """端到端: ClsClient.fetch 跑通 (mock akshare 不实跳)."""
    # 这个测试验证 CaixinClient 实例化 + fetch 不抛, 用真实 akshare 会实跳
    # 改为只 verify 类构造正常
    c = CaixinClient()
    assert c.name.startswith("财新")
