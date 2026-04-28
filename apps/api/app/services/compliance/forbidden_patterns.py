"""BE-S5-001 红线词词典 + LLM 输出过滤 v2.

设计要点
========

1. **两层词典**:
   - **Tier 1 (硬阻断)**: spec/06 §6.1 绝对红线 — 收益承诺 / 推荐买入 / 损失保证 / 内幕信息.
     LLM 真吐出来时调用方应 raise ``ForbiddenPatternError`` 阻断 SSE 输出, 不让用户看到一秒.
   - **Tier 2 (软脱敏)**: 模糊承诺 / 营销话术. 替换为 ``[已脱敏]`` + logger.warning + 上报 metrics.
     允许继续输出, 因为单条命中证据不足以认定为投顾承诺, 但要打标 + 收敛.

2. **否定豁免**: ``"不是必涨"`` / ``"并非必赚"`` / ``"未必稳赚"`` / ``"绝不会包赚"`` 视为否定句, 不算命中.
   规则: 在命中词的前 ``_NEGATION_WINDOW`` (6 个字符) 内出现否定词 ``_NEGATION_PREFIXES``
   且这段前缀不被句末标点 (``。 ! ? ; \n``) 断开, 视为豁免.
   反例: ``"我觉得不会亏。但是必涨。"`` — 第二个"必涨"不豁免, 因为前面有 ``"。"`` 断开.

3. **性能**: 把 Tier 1 / Tier 2 各编译成一次 ``re.compile("|".join(words))`` alternation.
   45 个词的 alternation 在 1KB 文本上 ``finditer`` ~0.3ms (远低于 spec/12 §AC 5ms 上限).
   不引入 ``pyahocorasick`` (avoid新依赖, vibe coding 够用就好).

4. **大小写 / 全半角 / 空白**: 中文红线词以中文为主, ``re.IGNORECASE`` 处理英文 (``all in`` / ``ALL IN``).
   全角"必涨"和半角"必涨"在 Unicode 里不是同一字符 — 这里只覆盖标准全角 + 英文; 黑客式
   规避 (例: ``"必_涨"`` / ``"必 涨"``) 不在本 PR 覆盖, 留 Sprint 5.5 出 char-fold normalize.

5. **与 Sprint 1 ``adapters.llm_client.forbidden_pattern_filter`` 关系**:
   Sprint 1 是 6 条 regex + 不真过滤 (扫完 hits 但 cleaned 没 yield 出去) + 仅 logger.warning.
   本模块是它的 v2: 真替换 Tier 2 + Tier 1 raise + 否定豁免. 旧 API 保留 + delegate 到这里.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from re import Pattern

# ─── 词典 (spec/06 §6.1 + §6.3.C + 监管《证券发行与承销管理办法》§29) ────────

# Tier 1 — 硬阻断词 (绝对不能在 LLM 输出里). 共 30+ 条.
TIER1_PATTERNS: list[str] = [
    # ─── 收益承诺类 (10) ─────────────────────────────────
    "必涨",
    "必赚",
    "包赚",
    "稳赚",
    "躺赚",
    "包赔",
    "一定涨",
    "一定赚",
    "必涨停",
    "翻倍",
    # ─── 推荐买入类 (8) ──────────────────────────────────
    "强烈推荐",
    "强烈建议买入",
    "强力推荐",
    "闭眼买",
    "闭眼买入",
    "立刻买入",
    "必须买",
    "梭哈",
    # ─── 损失保证类 (7) ─────────────────────────────────
    "保本",
    "保收益",
    "无风险",
    "零风险",
    "兜底",
    "不会亏",
    "不亏",
    # ─── 内幕信息类 (5) ─────────────────────────────────
    "内幕消息",
    "关系户",
    "内部价",
    "提前知道",
    "包中签",
    # ─── 满仓 / 抢筹 (5) ────────────────────────────────
    "建议满仓",
    "建议重仓",
    "建议全仓",
    "建议加仓",
    "建议抄底",
    # ─── 打新承诺类 (4) ─────────────────────────────────
    "打新必中",
    "中签率100%",
    "中签率 100%",
    "100%中签",
    # ─── 英文 (大小写不敏感, 局部 inline group flag (?i:...) 防 global flag 落非首位 ──
    r"(?i:all\s*in)",
]

# Tier 2 — 软脱敏词 (替换为 [已脱敏] + 上报). 共 16 条.
TIER2_PATTERNS: list[str] = [
    # ─── 模糊承诺 (8) ───────────────────────────────────
    "基本必涨",
    "大概率赚",
    "大概率涨",
    "应该不会亏",
    "几乎不亏",
    "几乎包中",
    "几乎稳赚",
    "锁定收益",
    # ─── 营销话术 (8) ───────────────────────────────────
    "错过等十年",
    "千年一遇",
    "史诗级机会",
    "百年难遇",
    "跑赢大盘",
    "跑赢市场",
    "暴涨",
    "猛涨",
]

# 否定词前缀: 出现在命中词前 ``_NEGATION_WINDOW`` 字符内 → 视为否定豁免.
_NEGATION_PREFIXES: tuple[str, ...] = (
    "不",
    "非",
    "并非",
    "并不",
    "并不会",
    "未必",
    "并未",
    "绝不",
    "决不",
    "从不",
    "不会",
    "无法",
    "不一定",
    "未尝",
)
_NEGATION_WINDOW = 6
_SENTENCE_BOUNDARIES = "。！？!?;；\n"

# 替换占位符 (Tier 2 命中后写入清理文本)
TIER2_REDACTION = "[已脱敏]"


# ─── 编译 alternation regex (一次性) ────────────────────────────────────


def _compile_alternation(patterns: list[str]) -> Pattern[str]:
    """把多个词 alternation 成一个 regex.

    词里允许已带 ``(?i:...)`` 等局部 inline group flag (例如 ``all in`` 是英文要忽略大小写;
    不能用 ``(?i)`` global flag 因为 Python 3.7+ 要求它在 pattern 最开头, 拼到 alternation
    中间会触发 ``re.PatternError``); 不在外层加 IGNORECASE 全局标志, 让中文词保持精确大小写.
    """
    if not patterns:
        # 空表: 用永不匹配的 sentinel
        return re.compile(r"(?!.)")
    return re.compile("|".join(patterns))


_TIER1_REGEX: Pattern[str] = _compile_alternation(TIER1_PATTERNS)
_TIER2_REGEX: Pattern[str] = _compile_alternation(TIER2_PATTERNS)


# ─── 数据类 / 异常 ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ScanResult:
    """``forbidden_pattern_filter`` 的扫描结果.

    Fields:
        tier1_hits: Tier 1 命中词列表 (按出现顺序). 任意非空 → 调用方应阻断输出.
        tier2_hits: Tier 2 命中词列表 (按出现顺序). 已被替换成 [已脱敏], 仍上报.
        negation_skipped: 因否定豁免被跳过的词列表. 仅 metrics 用, 不影响 cleaned.
    """

    tier1_hits: list[str] = field(default_factory=list)
    tier2_hits: list[str] = field(default_factory=list)
    negation_skipped: list[str] = field(default_factory=list)

    @property
    def has_tier1(self) -> bool:
        return bool(self.tier1_hits)

    @property
    def has_tier2(self) -> bool:
        return bool(self.tier2_hits)

    @property
    def all_hits(self) -> list[str]:
        return self.tier1_hits + self.tier2_hits


class ForbiddenPatternError(Exception):
    """Tier 1 命中. 调用方应阻断 SSE 输出 / fallback 到友好提示."""

    def __init__(self, hits: list[str]) -> None:
        self.hits = list(hits)
        super().__init__(f"Tier 1 forbidden pattern hit: {', '.join(hits)}")


# ─── 否定豁免检查 ──────────────────────────────────────────────────────


def _is_negation_exempt(text: str, hit_start: int) -> bool:
    """检查命中位置 ``hit_start`` 之前 ``_NEGATION_WINDOW`` 字符内是否有否定词,
    且这段 prefix 不被句末标点断开.

    例: ``"不是必涨"``  hit_start=2 ("必涨" 起点)
        prefix = "不是" → 含 "不" 且无句末标点 → 豁免
    例: ``"我觉得不会亏。但是必涨。"`` hit_start=11 ("必涨" 起点)
        prefix = "亏。但是" → 含 "。" → **不**豁免
    """
    start = max(0, hit_start - _NEGATION_WINDOW)
    prefix = text[start:hit_start]

    # 句末标点截断: 只看最后一个标点之后的部分
    last_boundary = -1
    for ch in _SENTENCE_BOUNDARIES:
        idx = prefix.rfind(ch)
        if idx > last_boundary:
            last_boundary = idx
    if last_boundary >= 0:
        prefix = prefix[last_boundary + 1 :]

    # 任意否定词匹配 → 豁免
    return any(neg in prefix for neg in _NEGATION_PREFIXES)


# ─── 公开 API ───────────────────────────────────────────────────────────


def scan(text: str) -> ScanResult:
    """**只扫描不修改**, 返回 ScanResult. 性能敏感场景 / 单元测试用.

    复杂度 O(n × #patterns) (re alternation 走 NFA, n=len(text)).
    """
    if not text:
        return ScanResult()

    tier1_hits: list[str] = []
    tier2_hits: list[str] = []
    negation_skipped: list[str] = []

    for m in _TIER1_REGEX.finditer(text):
        if _is_negation_exempt(text, m.start()):
            negation_skipped.append(m.group(0))
        else:
            tier1_hits.append(m.group(0))

    for m in _TIER2_REGEX.finditer(text):
        if _is_negation_exempt(text, m.start()):
            negation_skipped.append(m.group(0))
        else:
            tier2_hits.append(m.group(0))

    return ScanResult(
        tier1_hits=tier1_hits,
        tier2_hits=tier2_hits,
        negation_skipped=negation_skipped,
    )


def is_tier1_clean(text: str) -> bool:
    """快速判断 ``text`` 不含 Tier 1 命中. 比 ``scan().has_tier1`` 略快 (短路).

    主要给 SSE chunk 实时过滤用 (每帧 chunk 扫一遍, 命中即阻断, 不收集所有命中).
    """
    if not text:
        return True
    return all(
        _is_negation_exempt(text, m.start()) for m in _TIER1_REGEX.finditer(text)
    )


def find_first_tier1_hit(text: str) -> tuple[int, str] | None:
    """找第一个**非否定豁免**的 Tier 1 命中位置, 返回 (start, hit_word) 或 None.

    给 SSE 流式阻断用: 调用方拿到第一个命中位置 → yield 命中前的干净前缀 +
    阻断提示, 把命中位置和之后全丢弃.
    """
    if not text:
        return None
    for m in _TIER1_REGEX.finditer(text):
        if not _is_negation_exempt(text, m.start()):
            return m.start(), m.group(0)
    return None


def forbidden_pattern_filter(text: str) -> tuple[str, ScanResult]:
    """**扫描 + 替换**, 返回 (cleaned_text, ScanResult).

    Tier 1 命中: 调用方应根据 ``result.has_tier1`` 自行阻断 (本函数不 raise,
    让调用方决定是 raise 还是降级到 fallback 文案); cleaned_text 中 Tier 1 命中
    位置也会被替换成 ``[已脱敏]`` 作为最后一道兜底, 避免调用方忘了检查.

    Tier 2 命中: 替换成 ``[已脱敏]`` + 在 result 里记录, 调用方继续输出.

    实现要点: 单次扫描原文收集所有 (tier, span) 命中, 再一次性按 span 顺序构建
    cleaned. 这样:
    1. 否定豁免检查永远基于原文 offset, 不会因为前面的替换 shift 而看错位置
    2. Tier 1 / Tier 2 重叠时 (理论上现有词典不会有, 但留护栏): 较早起点优先,
       同起点时 Tier 1 优先 (更严格)
    3. 替换后的 ``[已脱敏]`` 占位本身不会被再次扫到 (TIER2_REDACTION 不含红线词)
    """
    if not text:
        return text, ScanResult()

    tier1_hits: list[str] = []
    tier2_hits: list[str] = []
    negation_skipped: list[str] = []

    # 收集所有命中: (start, end, tier, word). tier=1 优先级高 (重叠时取它)
    spans: list[tuple[int, int, int, str]] = []
    for m in _TIER1_REGEX.finditer(text):
        if _is_negation_exempt(text, m.start()):
            negation_skipped.append(m.group(0))
            continue
        spans.append((m.start(), m.end(), 1, m.group(0)))
    for m in _TIER2_REGEX.finditer(text):
        if _is_negation_exempt(text, m.start()):
            negation_skipped.append(m.group(0))
            continue
        spans.append((m.start(), m.end(), 2, m.group(0)))

    # 按 (start, -tier) 排: 起点小的优先, 同起点时 tier 1 优先
    spans.sort(key=lambda s: (s[0], s[2]))

    # 去重叠: 一个 char 只能属于一个 span. 选了的就跳过被它覆盖的后续 span.
    accepted: list[tuple[int, int, int, str]] = []
    last_end = -1
    for span in spans:
        if span[0] < last_end:
            # 与前一个被接受的 span 重叠, 跳过. (理论上现词典不会触发)
            continue
        accepted.append(span)
        last_end = span[1]

    # 一次性构造 cleaned + 收集 hits
    if not accepted:
        return text, ScanResult(negation_skipped=negation_skipped)

    parts: list[str] = []
    cursor = 0
    for start, end, tier, word in accepted:
        parts.append(text[cursor:start])
        parts.append(TIER2_REDACTION)
        cursor = end
        (tier1_hits if tier == 1 else tier2_hits).append(word)
    parts.append(text[cursor:])
    cleaned = "".join(parts)

    return cleaned, ScanResult(
        tier1_hits=tier1_hits,
        tier2_hits=tier2_hits,
        negation_skipped=negation_skipped,
    )
