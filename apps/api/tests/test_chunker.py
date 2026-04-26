"""BE-S2-004 — 文本切分器单测.

只覆盖 ``app/services/rag/chunker.py`` 纯函数:
- ``estimate_tokens``: CJK / 英文 / 混合 / 空串
- ``split_text``: 短文本直通 / 段落边界 / 单段超长按句切 / 单句超长硬截
  / overlap 行为 / char offset 正确性 / 入参校验
"""

from __future__ import annotations

import pytest

from app.services.rag.chunker import Chunk, estimate_tokens, split_text

# ─── estimate_tokens ────────────────────────────────────────────────────────


def test_estimate_tokens_empty_returns_zero() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_pure_cjk_one_per_char() -> None:
    assert estimate_tokens("招股书") == 3
    assert estimate_tokens("中国香港交易所新股") == 9


def test_estimate_tokens_pure_english_4_chars_per_token() -> None:
    # 12 chars → 12 / 4 = 3 tokens
    assert estimate_tokens("abcdefghijkl") == 3
    # min 1 token even for 1 char
    assert estimate_tokens("a") == 1


def test_estimate_tokens_mixed_cjk_and_english() -> None:
    # "招股书 prospectus" → 3 CJK + 12 ASCII (含空格) → 3 + 12//4 = 6
    assert estimate_tokens("招股书 prospectus") == 6


# ─── split_text — 入参校验 ─────────────────────────────────────────────────


def test_split_text_max_tokens_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        split_text("hi", max_tokens=0)


def test_split_text_overlap_negative_rejected() -> None:
    with pytest.raises(ValueError, match="overlap_tokens"):
        split_text("hi", max_tokens=10, overlap_tokens=-1)


def test_split_text_overlap_geq_max_rejected() -> None:
    with pytest.raises(ValueError, match="overlap_tokens"):
        split_text("hi", max_tokens=10, overlap_tokens=10)


def test_split_text_empty_returns_empty_list() -> None:
    assert split_text("", max_tokens=100) == []
    assert split_text("   \n\n\t  ", max_tokens=100) == []


# ─── split_text — 短文本直通 ─────────────────────────────────────────────────


def test_split_text_short_returns_single_chunk() -> None:
    text = "Brief paragraph that fits."
    chunks = split_text(text, max_tokens=100, overlap_tokens=10)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(text)
    assert chunks[0].token_count == estimate_tokens(text)


# ─── split_text — 段落边界切分 ──────────────────────────────────────────────


def test_split_text_paragraph_boundary_packing() -> None:
    """3 个段落, max=20 tokens; 应该切成 2-3 个 chunk, 不切碎单段."""
    p1 = "Paragraph one " * 5  # ~14 tokens
    p2 = "Section two body " * 5  # ~17 tokens
    p3 = "Risks overview text " * 5  # ~21 tokens (单段已超)
    text = f"{p1}\n\n{p2}\n\n{p3}"

    chunks = split_text(text, max_tokens=20, overlap_tokens=0)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.token_count > 0


def test_split_text_long_paragraph_splits_by_sentence() -> None:
    """单段超长 → 按句子继续切, 不会硬截单句."""
    text = (
        "First sentence describing IPO. "
        "Second sentence about pricing. "
        "Third sentence on use of proceeds. "
        "Fourth sentence on risk factors. "
        "Fifth sentence covering management. "
        "Sixth sentence with peer comparison. "
    ) * 3

    chunks = split_text(text, max_tokens=15, overlap_tokens=0)
    # 句子可识别 → 切出多个 chunk, 没单 chunk 远超 15*1.5
    assert len(chunks) >= 3
    for c in chunks:
        assert c.token_count <= 25


def test_split_text_overlap_introduces_repetition() -> None:
    """overlap > 0 时, 相邻 chunk 末尾 / 开头有共同 substring."""
    p1 = "Alpha sentence text " * 6
    p2 = "Bravo sentence text " * 6
    text = f"{p1}\n\n{p2}"

    chunks = split_text(text, max_tokens=20, overlap_tokens=10)
    assert len(chunks) >= 2
    # 第二段 chunk 起点字符 < 第一段 chunk 终点字符 (overlap 倒带)
    assert chunks[1].char_start < chunks[0].char_end


def test_split_text_zero_overlap_no_repetition() -> None:
    p1 = "Alpha sentence text " * 5
    p2 = "Bravo sentence text " * 5
    text = f"{p1}\n\n{p2}"

    chunks = split_text(text, max_tokens=20, overlap_tokens=0)
    assert len(chunks) >= 2
    # 段落边界严格分离 (允许 == 是因为 \n\n 在两段之间不计入 chunk)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt.char_start >= prev.char_end - 1


def test_split_text_chunk_order_and_offsets_monotonic() -> None:
    text = "\n\n".join(f"Chunk {i} text body words " * 4 for i in range(8))
    chunks = split_text(text, max_tokens=25, overlap_tokens=5)
    assert chunks
    for i, c in enumerate(chunks):
        assert isinstance(c, Chunk)
        assert 0 <= c.char_start <= c.char_end <= len(text)
        if i > 0:
            assert c.char_start >= chunks[i - 1].char_start


def test_split_text_cjk_paragraphs() -> None:
    """中文段落: 双换行段落边界 + 中文句号边界一起测."""
    text = (
        "本招股说明书旨在揭示新股发行人的基本面与风险因素。"
        "公司主营业务涵盖人工智能、大数据与云计算。"
        "本次募集资金主要用于研发投入与海外市场拓展。"
        "\n\n"
        "财务摘要显示, 公司近三年收入复合增长率约 35%。"
        "毛利率稳定在 45% 上下。"
        "净利润受研发开支影响有所波动。"
    )
    chunks = split_text(text, max_tokens=30, overlap_tokens=5)
    assert len(chunks) >= 2
    for kw in ["招股说明书", "财务摘要", "复合增长率"]:
        assert any(kw in c.text for c in chunks), kw


def test_split_text_huge_single_word_hard_truncates() -> None:
    """单个超长 token (无空格无标点) → 硬截字符, 不无限循环."""
    text = "a" * 5000  # 5000 / 4 = 1250 tokens, 远超 max=100
    chunks = split_text(text, max_tokens=100, overlap_tokens=0)
    assert len(chunks) >= 5
    for c in chunks:
        assert len(c.text) <= 400  # max_tokens * 4 = 400 chars
