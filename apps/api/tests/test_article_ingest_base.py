"""BE-S3-002: 文章 ingest 公共 base 模块单测.

主要测 ``IPOKeywordIndex`` 的关键词派生 + 反查命中逻辑 (dispatcher 的核心
依赖). 不需要 PG / Redis, 纯 in-memory 单元.
"""

from __future__ import annotations

from app.services.article_ingest.sources.base import IPOKeywordIndex


def test_index_derives_keywords_from_code_and_name() -> None:
    """``code`` (含后缀 + 不含后缀) + ``name`` (全名 + 短名) 都进关键词集."""
    idx = IPOKeywordIndex.from_rows(
        [
            ("00700.HK", "HK", "腾讯控股"),
            ("09660.HK", "HK", "地平线机器人-W"),
            ("301456.SZ", "A", "夜光明股份有限公司"),
        ]
    )
    assert len(idx) == 3

    # 命中规则: code (带后缀 / 不带后缀), name (全名 / 短名)
    hits = idx.match(title="腾讯控股 Q1 财报", summary=None)
    assert len(hits) == 1
    assert hits[0]["code"] == "00700.HK"

    # 短名命中 (-W 后缀剥)
    hits = idx.match(title="地平线机器人 IPO 解读", summary=None)
    assert hits[0]["code"] == "09660.HK"

    # code 命中 (不含后缀的简写, 财经文章常见)
    hits = idx.match(title="09660 招股价区间敲定", summary=None)
    assert hits[0]["code"] == "09660.HK"

    # 短名 (去 ``股份有限公司`` 后缀)
    hits = idx.match(title="夜光明 IPO 价格区间", summary=None)
    assert hits[0]["code"] == "301456.SZ"


def test_index_match_multi_ipo_in_one_article() -> None:
    """同篇文章命中多 IPO → 全部返回 (常见场景: 行业报告)."""
    idx = IPOKeywordIndex.from_rows(
        [("00700.HK", "HK", "腾讯控股"), ("01024.HK", "HK", "快手")]
    )
    hits = idx.match(title="腾讯控股 vs 快手 — 港股两大互联网龙头比较", summary=None)
    assert len(hits) == 2
    codes = {h["code"] for h in hits}
    assert codes == {"00700.HK", "01024.HK"}


def test_index_match_empty_when_no_hit() -> None:
    """无命中关键词 → 返回空 list (dispatcher 据此丢弃文章)."""
    idx = IPOKeywordIndex.from_rows([("00700.HK", "HK", "腾讯控股")])
    hits = idx.match(title="完全无关的财经新闻", summary="美联储加息分析")
    assert hits == []


def test_index_summary_is_searched_too() -> None:
    """``summary`` 字段也参与匹配, 不只 title."""
    idx = IPOKeywordIndex.from_rows([("00700.HK", "HK", "腾讯控股")])
    hits = idx.match(
        title="互联网行业一周回顾",
        summary="本周关键事件: 腾讯控股 Q1 业绩超预期",
    )
    assert len(hits) == 1
    assert hits[0]["code"] == "00700.HK"


def test_index_short_keyword_filtered() -> None:
    """单字 / 太短 (< 2 字符) 关键词不进索引, 防止误匹配通用词."""
    # 'A' 是单字符, 派生时不应被纳入
    idx = IPOKeywordIndex.from_rows([("A.HK", "HK", "A")])
    # 内部 _index 应该没把 'A' 这个单字关键词放进去
    assert "A" not in idx._index  # noqa: SLF001


def test_index_dedup_within_keyword_set() -> None:
    """同 IPO 派生关键词去重 (例如 code 全名 == code 不带后缀的少见情况)."""
    idx = IPOKeywordIndex.from_rows([("ABC", "A", "ABC")])
    # 'ABC' 既是 code 又是 name, 不能进索引两次报错
    assert len(idx) == 1
