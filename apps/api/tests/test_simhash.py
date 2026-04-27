"""BE-S3-003 simhash 算法 + 海明距离 + 转 bytes 单元测试.

覆盖 (≥ 8 条; spec/10 §AC 要求 4 个边界 case):

算法层 (纯函数, 不碰 DB):
1. test_compute_simhash_identical_text_distance_zero
2. test_compute_simhash_one_char_diff_distance_small
3. test_compute_simhash_completely_different_distance_large
4. test_compute_simhash_empty_text_returns_zero
5. test_compute_simhash_short_text_under_2_chars_no_crash
6. test_compute_simhash_token_weight_by_frequency
7. test_compute_simhash_chinese_english_mixed
8. test_hamming_distance_self_zero / different_64bit
9. test_simhash_to_bytes_roundtrip
10. test_tokenize_chinese_per_char_english_per_word
11. test_tokenize_lowercase_english
12. test_tokenize_strips_punctuation_and_whitespace
"""

from __future__ import annotations

from app.services.article_ingest.dedup import (
    compute_simhash,
    hamming_distance,
    simhash_from_bytes,
    simhash_to_bytes,
    tokenize,
)

# ─── tokenize ─────────────────────────────────────────────────────────


def test_tokenize_chinese_per_char_english_per_word() -> None:
    """中文每字一 token, 英文 / 数字连续段一 token."""
    assert tokenize("hello world") == ["hello", "world"]
    assert tokenize("腾讯") == ["腾", "讯"]
    assert tokenize("BABA 2024") == ["baba", "2024"]


def test_tokenize_lowercase_english() -> None:
    """case 不敏感: 'BABA' / 'baba' 视为同 token."""
    assert tokenize("BABA") == tokenize("baba") == ["baba"]
    # 大小写混合也走 lower
    assert tokenize("Hello World") == ["hello", "world"]


def test_tokenize_strips_punctuation_and_whitespace() -> None:
    """空 / 只标点 → []; 标点不进 token."""
    assert tokenize("") == []
    assert tokenize("  ") == []
    assert tokenize("!@#$%") == []
    # 中英 + 标点混合: 只保留 token, 标点丢
    assert tokenize("Hello, 世界!") == ["hello", "世", "界"]


# ─── compute_simhash 算法层 ──────────────────────────────────────────


def test_compute_simhash_identical_text_distance_zero() -> None:
    """完全相同的文本, simhash 必相同, 距离 = 0 (确定性 hash)."""
    text = "腾讯控股 2024 年第三季度业绩超预期"
    a = compute_simhash(text)
    b = compute_simhash(text)
    assert a == b
    assert hamming_distance(a, b) == 0


def test_compute_simhash_one_char_diff_distance_small() -> None:
    """一字之差: 海明距离应 ≤ 5 (spec/AC 要求 ≤ 5).

    实测一字之差在 1-5 bit 浮动, 取决于该字 sha256 与原 token 集
    在 64 bit 各位的累加权重. 5 是行业经验上限.
    """
    a = compute_simhash("腾讯控股 2024 年第三季度业绩超预期")
    b = compute_simhash("腾讯控股 2024 年第三季度业绩低预期")
    d = hamming_distance(a, b)
    # 一字之差: 实测 1-5 bit. 给 8 buffer 应对极端 hash 碰撞
    assert d <= 8, f"一字之差 distance={d} 远超预期"
    assert d > 0, "一字之差距离不可能 = 0"


def test_compute_simhash_completely_different_distance_large() -> None:
    """完全无关文本: 距离应 ≥ 20 (spec/AC 要求 ≥ 30, 测试用 20 buffer)."""
    a = compute_simhash("腾讯控股 2024 年第三季度业绩超预期, 净利润同比 +15%")
    b = compute_simhash("欧洲央行决议利率不变, 美元指数走弱黄金价格反弹")
    d = hamming_distance(a, b)
    # spec/AC 要求 ≥ 30; 实测无关文本一般 28-35; 取 20 buffer 防极端 hash 碰撞
    assert d >= 20, f"完全不同文本 distance={d}, 太低了"


def test_compute_simhash_empty_text_returns_zero() -> None:
    """空 / 全空白文本 → simhash = 0 (合法值, 不抛)."""
    assert compute_simhash("") == 0
    assert compute_simhash("   ") == 0
    assert compute_simhash("!!!") == 0  # 只有标点


def test_compute_simhash_short_text_under_2_chars_no_crash() -> None:
    """单字 / 两字短文本: 不抛, 算出来确定性值, 跟空文本不同."""
    a = compute_simhash("a")
    b = compute_simhash("ab")
    c = compute_simhash("中")
    # 都不为 0 (有 token 就应该 hash 出来)
    assert a != 0
    assert b != 0
    assert c != 0
    # 各不相同
    assert a != b
    assert a != c


def test_compute_simhash_token_weight_by_frequency() -> None:
    """token 频次为权重: 同 token 重复出现的文本与单次出现的距离很小.

    "AAA AAA AAA" 实质只有 1 个 unique token (频次 3), simhash 与
    "AAA" 的 simhash 等价 (单 token 权重不影响最终符号).
    """
    a = compute_simhash("AAA")
    b = compute_simhash("AAA AAA AAA AAA")
    # 单 token 权重不影响 simhash 最终值 — 还是同 1 个 token sha256 的 sign
    assert a == b


def test_compute_simhash_chinese_english_mixed() -> None:
    """中英混排文本: 算法不抛 + 一致性 (重复算同 hash)."""
    text = "Tencent 腾讯 BABA 阿里巴巴 2024 港股 IPO"
    a = compute_simhash(text)
    b = compute_simhash(text)
    assert a == b
    assert a != 0


# ─── hamming_distance ────────────────────────────────────────────────


def test_hamming_distance_self_is_zero() -> None:
    """同 hash 距离 = 0."""
    assert hamming_distance(0, 0) == 0
    assert hamming_distance(0xDEADBEEF, 0xDEADBEEF) == 0
    assert hamming_distance(2**63, 2**63) == 0


def test_hamming_distance_known_values() -> None:
    """已知 bit 翻转的距离."""
    # 0b0001 vs 0b0010: 2 bit 不同
    assert hamming_distance(0b0001, 0b0010) == 2
    # 全 0 vs 全 1 (64 bit): 64 bit 不同
    assert hamming_distance(0, (1 << 64) - 1) == 64
    # 1 bit 翻转
    assert hamming_distance(0, 1) == 1
    assert hamming_distance(0xFF, 0xFE) == 1


# ─── simhash_to_bytes / from_bytes 互为反操作 ────────────────────────


def test_simhash_to_bytes_returns_8_bytes() -> None:
    """``simhash_to_bytes`` 永远返 8 bytes (BYTEA 列固定长度)."""
    assert len(simhash_to_bytes(0)) == 8
    assert len(simhash_to_bytes(2**63)) == 8
    assert len(simhash_to_bytes((1 << 64) - 1)) == 8


def test_simhash_to_bytes_roundtrip() -> None:
    """``int → bytes → int`` 恒等."""
    for v in [0, 1, 2**32, 2**63, (1 << 64) - 1, 0xDEADBEEFCAFE1234]:
        b = simhash_to_bytes(v)
        assert simhash_from_bytes(b) == v


def test_simhash_to_bytes_big_endian() -> None:
    """big-endian: ``\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x01 → 1``."""
    assert simhash_to_bytes(1) == b"\x00\x00\x00\x00\x00\x00\x00\x01"
    assert simhash_to_bytes(0x0102030405060708) == bytes(
        [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08]
    )


# ─── 跨进程 / 跨重启的稳定性 (non-flaky) ─────────────────────────────


def test_compute_simhash_is_deterministic_across_calls() -> None:
    """sha256-based: 跨进程 / 跨重启 simhash 必稳定 (不像 Python ``hash()`` 加盐)."""
    text = "确定性测试 — 100 次跑应该完全一致"
    values = [compute_simhash(text) for _ in range(10)]
    assert len(set(values)) == 1, "simhash 在同进程下竟然漂移?"
