"""合规护栏测试: 黑名单关键词 / 免责声明 (对应 .cursor/rules/50-compliance.mdc)."""

from __future__ import annotations

from app.adapters.llm_client import (
    DISCLAIMER,
    ensure_disclaimer,
    forbidden_pattern_filter,
)


def test_forbidden_pattern_filter_catches_buy_advice() -> None:
    text = "建议满仓买入这只股票, 必涨"
    cleaned, hits = forbidden_pattern_filter(text)
    assert len(hits) >= 2
    assert "建议满仓" not in cleaned
    assert "必涨" not in cleaned


def test_forbidden_pattern_clean_text_unchanged() -> None:
    text = "本公司近三年营收稳健, PE 约 18 倍, 处于行业中位。"
    cleaned, hits = forbidden_pattern_filter(text)
    assert cleaned == text
    assert hits == []


def test_ensure_disclaimer_appends_when_missing() -> None:
    text = "这是一段分析"
    result = ensure_disclaimer(text)
    assert "不构成投资建议" in result
    assert result.endswith(DISCLAIMER.rstrip())


def test_ensure_disclaimer_skips_when_present() -> None:
    text = "分析结论: 风险大于收益。本工具不构成投资建议。"
    result = ensure_disclaimer(text)
    assert result.count("不构成投资建议") == 1
