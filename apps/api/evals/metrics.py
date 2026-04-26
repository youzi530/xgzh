"""三大评测指标的"纯函数"实现.

- ``compute_recall_at_5``: 输入 retrieved chunks + expected keywords, 输出 (hit?, 命中的 keyword 列表)
- ``compute_hallucination``: 输入 ground_truth_facts + answer_text + citations, 输出 (score, 漏掉/幻觉事实列表)

为什么把指标计算从 runner 里拆出来
==================================
1. **可测**: 指标公式独立单测, 不卷 LLM / PG 进来
2. **可复用**: end_to_end runner 里能直接拼这些函数; 未来加 in-line 或 online
   监控 (spec Sprint 4+) 也能直接 import
3. **公式可见**: spec/04 §2.5 给的指标"召回@5 / 幻觉率"在文字上简单, 但工程实现
   有"哪些算命中" / "事实 substring 还是 token 级匹配"的细节, 必须把口径锁进
   pure function 里走 PR review, 不能藏在 runner 里
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


def compute_recall_at_5(
    retrieved_chunks: list[dict[str, Any]],
    *,
    expected_keywords: Iterable[str],
    expected_doc_ids: Iterable[str] | None = None,
) -> tuple[bool, list[str]]:
    """召回@5 命中口径.

    命中规则 (spec/04 §2.5; spec/09 §S2 §评测集):
    1. 取 top5 chunk (上层调用方需保证 ``len(retrieved_chunks) ≤ 5``;
       不强校验, 只取前 5 计算)
    2. 如 ``expected_doc_ids`` 非空: top5 中任一 chunk ``doc_id`` ∈ 集合 → hit
    3. 否则 (默认): top5 中任一 chunk ``text`` 包含任一 ``expected_keyword``
       (大小写 / 空白不敏感) → hit

    返回 ``(命中, 命中 keyword 列表)``. 列表用于 reporter 展示 "命中是因为哪个词".
    keyword 比较使用 lower-case + ``str.casefold`` 做中英混排兜底.
    """
    top5 = retrieved_chunks[:5]
    if not top5:
        return False, []

    expected_doc_id_set = {d.strip() for d in (expected_doc_ids or []) if d and d.strip()}
    if expected_doc_id_set:
        for c in top5:
            if str(c.get("doc_id", "")).strip() in expected_doc_id_set:
                return True, [str(c.get("doc_id", "")).strip()]

    keywords = [k.strip().casefold() for k in expected_keywords if k and k.strip()]
    if not keywords:
        return False, []

    matched: list[str] = []
    for c in top5:
        text_lower = str(c.get("text", "") or "").casefold()
        if not text_lower:
            continue
        for kw in keywords:
            if kw in text_lower and kw not in matched:
                matched.append(kw)
    return (len(matched) > 0), matched


# ─── 幻觉率 (基于"答案中的事实 vs citations 中可查事实"的字符串近似匹配) ────


# 数字 / 日期 / 百分比 / 货币: 这些是金融答案里最容易被 LLM 编造的高危事实.
# 关键支持: 中文日期允许 " 年 / 月 / 日 " 之间有空格 (LLM 输出常见 "2018 年 9 月 20 日"),
# 金额允许 "数字 + 货币" 或 "货币 + 数字" 双向.
_FACT_PATTERN = re.compile(
    r"\d+(?:\.\d+)?%|"  # 百分比 12.5%
    r"\d{4}\s*年(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*日)?|"  # 中文日期 (允许空格)
    r"\d{4}-\d{2}-\d{2}|"  # ISO 日期
    r"\d+(?:[\.,]\d+)*\s*(?:亿|万|百万|千万)\s*(?:港元|美元|人民币|RMB|HKD|USD)?|"  # 数字+单位+(可选货币)
    r"\d+(?:[\.,]\d+)*\s*(?:港元|美元|人民币|RMB|HKD|USD)|"  # 数字+货币 (无单位)
    r"(?:港元|美元|人民币|RMB|HKD|USD)\s*\d+(?:[\.,]\d+)*(?:\s*(?:亿|万|百万|千万))?"  # 货币+数字
)


def _normalize_for_match(text: str) -> str:
    """归一化: 全角逗号/句号 → 半角, NFKC, casefold. 让 ``5,000万`` 与 ``5000万`` 等价."""
    import unicodedata

    s = unicodedata.normalize("NFKC", text or "")
    s = s.replace(",", "").replace(" ", "").replace("　", "")
    return s.casefold()


def extract_atomic_facts(text: str) -> list[str]:
    """从答案文本里抽"原子事实" (高危的数字/日期/金额).

    口径:
    - regex 抽匹配片段, 去重保序
    - 仅做粗粒度抽取; LLM-as-judge 阶段会做更细的判定. 本函数目的是给一个"快速字符级幻觉率"baseline
    """
    if not text:
        return []
    seen: set[str] = set()
    facts: list[str] = []
    for m in _FACT_PATTERN.findall(text):
        norm = _normalize_for_match(m)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        facts.append(m)
    return facts


def compute_hallucination(
    *,
    answer_text: str,
    citations: list[dict[str, Any]],
    ground_truth_facts: list[str],  # 当前 baseline 只用作 reporter 字段; 留给 LLM-judge
) -> tuple[float, list[str]]:
    """字符级幻觉率 (端到端模式 baseline).

    设计选择 (与 spec/04 §2.5 抽检 100 条人工核对的"事实是否在引用中"对齐):
    - **空答案**: ``score=0.0`` (没说就没幻觉; LLM-as-judge 里再扣"不答"的分)
    - **citation snippet 池**: 把所有 citation 的 snippet/text 拼接成一个大字符
      串, 在里面 ``substring contains`` 抽取出的原子事实是否出现
    - **抽取范围**: 只抽 ``extract_atomic_facts`` 返回的"硬事实" (数字/日期/金额/百分比);
      泛义词 (如 "互联网"、"主要业务") 走 LLM-as-judge 处理. 故意不混入
      ``ground_truth_facts``: 它们是人工标注的"应该提及的事实", 与"答案中已说出但
      无据的事实"不是一回事 (前者考核覆盖度, 后者考核幻觉率, 两个维度独立)
    - **罚分公式**: ``score = num_unbacked_facts / num_extracted_facts``
    - 返回 ``(score, unbacked_facts)``: ``unbacked_facts`` 给报告里 case-level 列出

    备注: 本函数是 baseline, 不会捕获"语义幻觉" (例如答案换说法但意思错). 真捕获
    交给 ``judge.LLMJudge``; baseline 用于 CI 阈值告警和无 LLM 时的兜底.

    ``ground_truth_facts`` 参数当前未直接参与计算, 保留是为了:
    1. 函数签名稳定 (端到端 runner 调用方不变)
    2. 后续 Sprint 3+ 引入 "覆盖度" 子指标时直接用同一签名
    """
    _ = ground_truth_facts  # 当前 baseline 未直接用; 保留供后续覆盖度指标
    text = answer_text.strip()
    if not text:
        return 0.0, []

    citation_pool_parts: list[str] = []
    for c in citations:
        snippet = c.get("snippet") or c.get("text") or ""
        if snippet:
            citation_pool_parts.append(str(snippet))
    citation_pool = _normalize_for_match("\n".join(citation_pool_parts))

    atomic = extract_atomic_facts(text)
    if not atomic:
        return 0.0, []

    seen: set[str] = set()
    candidate_facts: list[str] = []
    for f in atomic:
        norm = _normalize_for_match(f)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        candidate_facts.append(f)

    unbacked: list[str] = []
    for f in candidate_facts:
        norm = _normalize_for_match(f)
        if norm and norm not in citation_pool:
            unbacked.append(f)

    score = len(unbacked) / len(candidate_facts)
    return round(score, 4), unbacked


__all__ = [
    "compute_hallucination",
    "compute_recall_at_5",
    "extract_atomic_facts",
]
