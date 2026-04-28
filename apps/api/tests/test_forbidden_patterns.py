"""BE-S5-001 红线词词典 + forbidden_pattern_filter 单元测.

覆盖矩阵 (≥ 12 case):

A. 词典覆盖
1.  Tier 1 收益承诺词命中 (``必涨``, ``包赚`` 等)
2.  Tier 1 推荐买入词命中 (``强烈推荐``, ``闭眼买入`` 等)
3.  Tier 1 损失保证词命中 (``保本``, ``零风险`` 等)
4.  Tier 1 内幕信息词命中 (``内幕消息``, ``关系户`` 等)
5.  Tier 1 英文 (``all in`` 大小写不敏感)
6.  Tier 2 模糊承诺命中 (``大概率赚``)
7.  Tier 2 营销话术命中 (``千年一遇``)

B. 否定豁免
8.  紧贴否定 ``"不是必涨"`` → 豁免
9.  跨标点不豁免 ``"不会亏。但是必涨"`` → 第二个词不豁免
10. 多个否定词 ``"未必稳赚"`` / ``"绝不会包赚"`` → 豁免
11. 远离否定 (≥ 6 字) ``"不是这样的我觉得必涨"`` → 不豁免

C. 替换 / 多命中
12. 多 Tier 1 同句 → 全部替换成 [已脱敏]
13. Tier 1 + Tier 2 混合 → 都替换, hit lists 各自正确
14. 重复命中 → 都收集, 次序保留

D. 边界
15. 空字符串 / 纯空白 → 不命中
16. 长文本性能 (5KB) → < 5ms
17. is_tier1_clean 短路: Tier 2 命中但 Tier 1 干净 → True
"""

from __future__ import annotations

import time

import pytest

from app.services.compliance import (
    TIER1_PATTERNS,
    TIER2_PATTERNS,
    ForbiddenPatternError,
    forbidden_pattern_filter,
    is_tier1_clean,
    scan,
)
from app.services.compliance.forbidden_patterns import TIER2_REDACTION

# ─── A. 词典覆盖 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected_word",
    [
        ("这只新股必涨,大家都说能赚", "必涨"),
        ("我们觉得这次包赚不赔", "包赚"),
        ("稳赚的机会千万不要错过", "稳赚"),
        ("躺赚十年", "躺赚"),
        ("一定涨到三位数", "一定涨"),
    ],
)
def test_tier1_yield_promise_words_hit(text: str, expected_word: str) -> None:
    cleaned, result = forbidden_pattern_filter(text)
    assert result.has_tier1
    assert expected_word in result.tier1_hits
    assert TIER2_REDACTION in cleaned


def test_tier1_buy_recommendation_words_hit() -> None:
    text = "强烈推荐这只票, 闭眼买就行"
    cleaned, result = forbidden_pattern_filter(text)
    assert "强烈推荐" in result.tier1_hits
    assert "闭眼买" in result.tier1_hits
    assert cleaned.count(TIER2_REDACTION) == 2


def test_tier1_loss_guarantee_words_hit() -> None:
    text = "本产品保本无风险零风险打底"
    _, result = forbidden_pattern_filter(text)
    # ``保本`` / ``无风险`` / ``零风险`` 三命中
    assert "保本" in result.tier1_hits
    assert "无风险" in result.tier1_hits
    assert "零风险" in result.tier1_hits


def test_tier1_insider_info_words_hit() -> None:
    text = "我有内幕消息, 关系户都已经下单"
    _, result = forbidden_pattern_filter(text)
    assert "内幕消息" in result.tier1_hits
    assert "关系户" in result.tier1_hits


@pytest.mark.parametrize("english_phrase", ["all in", "ALL IN", "All In", "all   in"])
def test_tier1_english_all_in_case_insensitive(english_phrase: str) -> None:
    text = f"建议大家直接 {english_phrase} 这只 IPO"
    _, result = forbidden_pattern_filter(text)
    assert result.has_tier1
    # 命中词大小写保留原样
    assert any(english_phrase.lower() in h.lower() for h in result.tier1_hits)


def test_tier2_vague_promise_hit() -> None:
    text = "这次新股大概率赚, 应该不会亏"
    cleaned, result = forbidden_pattern_filter(text)
    assert "大概率赚" in result.tier2_hits
    assert "应该不会亏" in result.tier2_hits
    # Tier 1 干净
    assert not result.has_tier1
    assert TIER2_REDACTION in cleaned


def test_tier2_marketing_talk_hit() -> None:
    text = "千年一遇的史诗级机会, 错过等十年"
    _, result = forbidden_pattern_filter(text)
    assert "千年一遇" in result.tier2_hits
    assert "史诗级机会" in result.tier2_hits
    assert "错过等十年" in result.tier2_hits


# ─── B. 否定豁免 ──────────────────────────────────────────────────────


def test_negation_inline_exempts_hit() -> None:
    """``不是必涨`` → 豁免, 不算命中."""
    text = "这只新股不是必涨,大家要谨慎"
    cleaned, result = forbidden_pattern_filter(text)
    assert not result.has_tier1
    assert "必涨" in result.negation_skipped
    # 文本不被替换
    assert TIER2_REDACTION not in cleaned


def test_negation_cross_sentence_does_not_exempt() -> None:
    """``"不会亏。但是必涨"`` → 第二个词不豁免, 因为前面有句末标点."""
    text = "这次不会亏。但是必涨!"
    _, result = forbidden_pattern_filter(text)
    # ``必涨`` 不豁免, 算命中
    assert "必涨" in result.tier1_hits
    # ``不会亏`` 自身就在 Tier 1 词典里 (``不亏`` ≠ ``不会亏`` 但词典也含 ``不会亏``)
    # 这里 ``不会亏`` 起点 2, 前面只有 ``这次`` 没否定词, 所以这条本身就命中
    # 注意: 不要依赖 ``不会亏`` 自带否定语义豁免自己 — 那是词典级而不是上下文级
    assert "不会亏" in result.tier1_hits


@pytest.mark.parametrize(
    "text,exempt_word",
    [
        ("这次未必稳赚", "稳赚"),
        ("我们绝不会包赚", "包赚"),
        ("我并非要必涨", "必涨"),
        ("基本上不一定涨到那么高", "一定涨"),
    ],
)
def test_negation_various_prefixes_exempt(text: str, exempt_word: str) -> None:
    _, result = forbidden_pattern_filter(text)
    assert exempt_word not in result.tier1_hits
    assert exempt_word in result.negation_skipped


def test_negation_too_far_does_not_exempt() -> None:
    """否定词距离命中词 > 6 字符 → 不豁免."""
    text = "我并不认为这家公司这次会必涨,但我朋友坚信必涨。"
    _, result = forbidden_pattern_filter(text)
    # ``并不...必涨`` 之间隔了 7 个字 (``认为这家公司这次会``), 远超 6 字 window
    # 第一个 ``必涨`` 不豁免, 第二个独立句子也不豁免
    assert result.tier1_hits.count("必涨") == 2


# ─── C. 替换 / 多命中 ─────────────────────────────────────────────────


def test_multiple_tier1_replacements_all_redacted() -> None:
    text = "必涨稳赚保本兜底"
    cleaned, result = forbidden_pattern_filter(text)
    assert len(result.tier1_hits) == 4
    # 全部替换为占位
    assert "必涨" not in cleaned
    assert "稳赚" not in cleaned
    assert "保本" not in cleaned
    assert "兜底" not in cleaned
    assert cleaned.count(TIER2_REDACTION) == 4


def test_mixed_tier1_and_tier2_independent_collection() -> None:
    text = "我觉得这次必涨, 大概率赚, 千年一遇的机会"
    cleaned, result = forbidden_pattern_filter(text)
    assert result.tier1_hits == ["必涨"]
    assert set(result.tier2_hits) == {"大概率赚", "千年一遇"}
    # 都被替换
    assert cleaned.count(TIER2_REDACTION) == 3


def test_repeated_hits_preserve_order() -> None:
    text = "必涨!必涨!必涨!"
    _, result = forbidden_pattern_filter(text)
    assert result.tier1_hits == ["必涨", "必涨", "必涨"]


# ─── D. 边界 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["", "   ", "\n\t\n", "这是一段完全合规的中性描述。"])
def test_clean_text_no_hit(text: str) -> None:
    cleaned, result = forbidden_pattern_filter(text)
    assert cleaned == text
    assert not result.has_tier1
    assert not result.has_tier2


def test_long_text_performance_under_5ms() -> None:
    """5KB 文本扫描 < 5ms (spec/12 §AC).

    取 mean(10 次) 而非 single-shot 让结果稳: macOS GIL / GC 抖动可能让单次 > 5ms,
    但稳定均值应 < 5ms.
    """
    base = "这是一段中性的新股分析。" * 200  # ~5KB
    base += "其中包含必涨这种红线词作为压测."

    times: list[float] = []
    for _ in range(10):
        start = time.perf_counter()
        cleaned, result = forbidden_pattern_filter(base)
        times.append(time.perf_counter() - start)

    mean_ms = sum(times) / len(times) * 1000
    assert mean_ms < 5, f"mean={mean_ms:.2f}ms > 5ms"
    # 锁住至少能正确命中
    assert "必涨" in result.tier1_hits


def test_is_tier1_clean_short_circuit_tier2_only() -> None:
    """Tier 2 命中但 Tier 1 干净 → ``is_tier1_clean`` = True."""
    text = "这次新股大概率赚 (Tier 2 only)"
    assert is_tier1_clean(text) is True
    # 但 scan 仍能拿到 Tier 2
    result = scan(text)
    assert result.has_tier2
    assert not result.has_tier1


def test_is_tier1_clean_tier1_returns_false() -> None:
    text = "这次新股必涨"
    assert is_tier1_clean(text) is False


def test_is_tier1_clean_negation_treats_as_clean() -> None:
    """否定豁免后 Tier 1 不算命中."""
    text = "这次新股不是必涨"
    assert is_tier1_clean(text) is True


def test_forbidden_pattern_error_carries_hits() -> None:
    """ForbiddenPatternError 把命中词带出来给调用方降级用."""
    err = ForbiddenPatternError(["必涨", "稳赚"])
    assert err.hits == ["必涨", "稳赚"]
    assert "必涨" in str(err)
    assert "稳赚" in str(err)


def test_dictionary_size_meets_spec() -> None:
    """spec/12 §AC: Tier 1 ≥ 30 条, Tier 2 ≥ 15 条."""
    assert len(TIER1_PATTERNS) >= 30, f"Tier 1 only {len(TIER1_PATTERNS)} patterns"
    assert len(TIER2_PATTERNS) >= 15, f"Tier 2 only {len(TIER2_PATTERNS)} patterns"
