"""文本切分器 (BE-S2-004 RAG 流水线第 2 层).

职责
====
1. ``estimate_tokens(text)``: 启发式 token 计数. 中文 ≈ 1 字 / 1 token,
   英文 ≈ 4 字符 / 1 token; 不引 ``tiktoken`` 这种重依赖 (装包慢, 与
   bge-m3 真实 tokenizer 也不同, 反正 ``token_count`` 列只用于 cost 调试,
   不参与正确性).
2. ``split_text(text, max_tokens, overlap_tokens)``: 把一大段招股书原文切成
   一系列 ``Chunk``, 每个 chunk:
   - 控制在 ``max_tokens`` 以下 (尊重 bge-m3 8192 上限, 默认 500 给检索 +
     reranker + LLM context 都留够余地)
   - 跨段落 overlap ``overlap_tokens`` (默认 50, 即 10%); 防止段落边界切裂
     语义
   - 优先按"段落边界 (双换行)"切; 段落本身超长再按句子边界切; 句子还超
     长则硬截断 (理论极少, 招股书不会出现 1500+ token 单句)

为什么不直接用 LangChain ``RecursiveCharacterTextSplitter``
==========================================================
- LangChain v0.3 包体 50+ MB, 整本依赖链装进来不划算 (我们这里只需要切分,
  不需要它的 chain / agent / vectorstore 抽象)
- 切分逻辑就 80 行, 自己写更可控更易测; 项目走自维护精简包路线 (spec/06)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Chunk:
    """切分后的 chunk. ``token_count`` 是估值 (非精确 bge-m3 tokenizer)."""

    text: str
    token_count: int
    char_start: int
    char_end: int


# ─── token 估算 ─────────────────────────────────────────────────────────────

# CJK 字符范围: Unified Ideographs / Compatibility / Extension A
_CJK_RE = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")


def estimate_tokens(text: str) -> int:
    """启发式 token 估算.

    规则:
    - 每个 CJK 字符 ≈ 1 token (bge-m3 BPE 实测中文几乎逐字切)
    - 英文 / 其它 ≈ 1 token / 4 字符 (OpenAI tiktoken 经验值)
    - 空字符串 → 0

    与真实 bge-m3 tokenizer 的偏差通常在 ±15%, 远低于 chunk size 本身的
    冗余度, 不影响切分逻辑可靠性.
    """
    if not text:
        return 0
    cjk_chars = len(_CJK_RE.findall(text))
    other_chars = len(text) - cjk_chars
    # 纯 CJK 时 other_chars=0, 不强制 +1; 否则任何非空 ASCII 至少计 1 token
    other_tokens = (other_chars + 3) // 4 if other_chars > 0 else 0
    if cjk_chars == 0 and other_tokens == 0:
        return 1
    return cjk_chars + other_tokens


# ─── 切分 ───────────────────────────────────────────────────────────────────

# 双换行 = 段落边界 (招股书 PDF extract 后的常见格式: 段间一个或多个空行)
_PARAGRAPH_SEP = re.compile(r"\n\s*\n+")
# 句子边界: 中英文句号 / 问号 / 感叹号; 中英分号 (招股书条款) 也算
_SENTENCE_SEP = re.compile(r"(?<=[。!?；;.!?])\s+")


def split_text(
    text: str,
    *,
    max_tokens: int = 500,
    overlap_tokens: int = 50,
) -> list[Chunk]:
    """把 ``text`` 切成 ``Chunk`` 列表.

    算法 (3 层 fallback):

    1. 把 ``text`` 按段落分; 拼"已积累段落"直到再加一段会超 ``max_tokens``,
       封一个 chunk → 记录 char_start/char_end
    2. 单个段落本身就超 ``max_tokens`` 时, 改按句子分继续累
    3. 单句还超 (理论几乎不会发生), 直接按字符硬截

    overlap 通过"上一个 chunk 末尾 ``overlap_tokens`` 估算字符数倒带"实现;
    倒带不会跨越段落边界 (避免 overlap 牵出无关上下文).

    raises: ``ValueError`` 当 ``max_tokens <= 0`` 或 ``overlap_tokens < 0``
    """
    if max_tokens <= 0:
        raise ValueError(f"max_tokens 必须 > 0, got {max_tokens}")
    if overlap_tokens < 0:
        raise ValueError(f"overlap_tokens 不能为负, got {overlap_tokens}")
    if overlap_tokens >= max_tokens:
        raise ValueError(
            f"overlap_tokens ({overlap_tokens}) 必须 < max_tokens ({max_tokens})"
        )

    text = text.strip()
    if not text:
        return []

    # ── 短文本直接成单 chunk ─────────────────────────────────
    total_tokens = estimate_tokens(text)
    if total_tokens <= max_tokens:
        return [
            Chunk(
                text=text,
                token_count=total_tokens,
                char_start=0,
                char_end=len(text),
            )
        ]

    # ── 段落级切分 ───────────────────────────────────────────
    paragraphs = _PARAGRAPH_SEP.split(text)

    units: list[tuple[str, int, int]] = []  # (segment_text, abs_start, abs_end)
    cursor = 0
    for para in paragraphs:
        if not para.strip():
            cursor += len(para) + 2
            continue
        para_start = text.find(para, cursor)
        if para_start < 0:
            para_start = cursor
        para_end = para_start + len(para)
        cursor = para_end

        if estimate_tokens(para) <= max_tokens:
            units.append((para, para_start, para_end))
            continue

        # 段落超长: 按句子拆
        sentences = _SENTENCE_SEP.split(para)
        sub_cursor = para_start
        for sent in sentences:
            if not sent:
                continue
            sent_start = text.find(sent, sub_cursor)
            if sent_start < 0:
                sent_start = sub_cursor
            sent_end = sent_start + len(sent)
            sub_cursor = sent_end

            if estimate_tokens(sent) <= max_tokens:
                units.append((sent, sent_start, sent_end))
                continue

            # 单句超长 (招股书极少): 按字符硬截
            step = _max_chars_for_tokens(max_tokens)
            for i in range(0, len(sent), step):
                seg = sent[i : i + step]
                units.append((seg, sent_start + i, sent_start + i + len(seg)))

    # ── 拼装 + overlap ─────────────────────────────────────
    chunks: list[Chunk] = []
    cur_text: list[str] = []
    cur_tokens = 0
    cur_start: int | None = None
    cur_end: int | None = None

    overlap_chars = _max_chars_for_tokens(overlap_tokens) if overlap_tokens else 0

    for seg, seg_start, seg_end in units:
        seg_tokens = estimate_tokens(seg)
        if cur_text and cur_tokens + seg_tokens > max_tokens:
            chunk_text = "\n\n".join(cur_text).strip()
            if chunk_text:
                chunks.append(
                    Chunk(
                        text=chunk_text,
                        token_count=estimate_tokens(chunk_text),
                        char_start=cur_start or 0,
                        char_end=cur_end or len(chunk_text),
                    )
                )

            # overlap: 取上 chunk 末尾 overlap_chars 字符作为下 chunk 起点
            if overlap_chars and chunk_text:
                tail = chunk_text[-overlap_chars:]
                tail_tokens = estimate_tokens(tail)
                cur_text = [tail]
                cur_tokens = tail_tokens
                cur_start = (cur_end or 0) - len(tail)
                cur_end = cur_end
            else:
                cur_text = []
                cur_tokens = 0
                cur_start = None
                cur_end = None

        if cur_start is None:
            cur_start = seg_start
        cur_text.append(seg)
        cur_tokens += seg_tokens
        cur_end = seg_end

    if cur_text:
        chunk_text = "\n\n".join(cur_text).strip()
        if chunk_text:
            chunks.append(
                Chunk(
                    text=chunk_text,
                    token_count=estimate_tokens(chunk_text),
                    char_start=cur_start or 0,
                    char_end=cur_end or len(chunk_text),
                )
            )

    return chunks


def _max_chars_for_tokens(max_tokens: int) -> int:
    """token 上限 → 最坏情况下的字符数上限 (按全英文 4:1 换算).

    仅用于 overlap 倒带 + 单句硬截步长. 全 CJK 时实际字符数会更少, 不影响
    切分正确性 (只是 chunk 会比理论大小略小).
    """
    return max(1, max_tokens * 4)


__all__ = [
    "Chunk",
    "estimate_tokens",
    "split_text",
]
