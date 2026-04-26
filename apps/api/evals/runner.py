"""评测 runner: 三种 mode 的核心编排.

mode 设计 (CI 友好 → 真测试)
============================
1. ``keyword`` (离线, 不调 IO): 仅做 schema 校验 + ``reference_answer`` 包含
   ``expected_keywords`` 自检. 主要目的:
   - 让 CI 在 *没有 PG 也没有 LLM key* 的环境里能跑通 ``make eval-sprint2-smoke``,
     确保数据集本身没坏 (新加 case 时 keyword / reference_answer 是否对得上)
   - 不算 recall_at_5 / hallucination / judge 任何指标 (None)

2. ``retrieval`` (依赖 PG, 不依赖 LLM): 对每条 case 调
   ``app.services.rag.hybrid_search.hybrid_search`` 拿 top5 chunk → 算
   recall@5. 这是 spec/04 §2.5 第一档指标的离线评估口径
   - 不调 LLM, 不计算 hallucination_score / judge_score
   - 适合 baseline / 数据集打分 / Prompt 改动前的 RAG 能力 quick check

3. ``end_to_end`` (依赖 PG + LLM): 在 retrieval 基础上额外:
   - 用 hybrid_search 的 top5 当 context, 调 ``chat()`` 让 LLM 直接回答 query
   - 把 LLM 答案过 ``compute_hallucination`` (字符级 baseline) + 可选
     ``LLMJudge.judge`` (1-5 分语义判定)
   - 算"幻觉率"和"judge mean score"两条指标
   - **故意不调 ``graph.run``**: 主循环涉及写 ``chat_*`` 4 张表的事务, 评测每条
     case 都开一个 chat_session 会污染评测库; 评测用一个轻量 ``compose_eval_prompt``
     直接拼 system + context + query, 测 RAG + LLM 整体回答能力, 与 spec/04
     §2.5 评估口径一致

并发策略
========
- ``asyncio.Semaphore(concurrency)`` 控制单机并发上限 (默认 4, 可在 CLI 调).
  上游 LLM provider 普遍 RPM 60-200, 8 并发以下基本不撞 rate limit
- 每条 case 内部串行 (检索 → LLM → judge), 失败不阻塞其他 case
- 全局错误兜底: 单 case 异常仅落 ``EvalCaseResult.error``, 不抛上层
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from app.adapters.llm_client import LLMConfigError, LLMError, chat
from app.core.config import get_settings
from app.core.logging import logger
from app.db.base import get_session_factory
from app.services.rag.hybrid_search import hybrid_search
from evals.judge import LLMJudge
from evals.metrics import compute_hallucination, compute_recall_at_5
from evals.schema import (
    EvalCase,
    EvalCaseResult,
    EvalMode,
    RunReport,
    load_cases,
)

# 端到端模式给 LLM 的系统提示, 与 spec/04 §3 ReAct prompt 精神一致 (但更精简,
# 不带 tool use 决策, 因为 evaluation 是把 retrieved chunks 直接喂入 prompt)
_EVAL_SYSTEM_PROMPT = """你是一名严谨的港股 / A 股 IPO 投研助手。请基于下方提供的招股书 / 公司研究片段回答用户问题。

约束:
1. 必须基于 context 中的事实回答; 不允许编造 context 之外的具体数字 / 日期 / 名称
2. 引用 context 片段时使用 ``[N]`` 标号, N 对应 context 列表中的序号
3. 中文回答, 不超过 250 字; 数字和日期保留原始表述
4. 如果 context 不足以回答, 直接说 "现有招股书 / 资料未披露此信息" 而非编造"""

_EVAL_USER_TEMPLATE = """## 用户问题
{query}

## 检索到的招股书片段 (context, 按相关度排序)
{context_block}

请按照系统提示中的引用规则回答问题。"""


def _format_context(retrieved_chunks: list[dict[str, Any]]) -> str:
    """把 hybrid_search top5 chunk 格式化进 prompt 的 context 段."""
    if not retrieved_chunks:
        return "(无)"
    lines: list[str] = []
    for i, c in enumerate(retrieved_chunks[:5], start=1):
        doc_id = c.get("doc_id", "?")
        page = c.get("page")
        text = (c.get("text") or "").strip()
        if len(text) > 600:
            text = text[:600] + "…"
        page_part = f" P{page}" if page else ""
        lines.append(f"[{i}] {doc_id}{page_part}: {text}")
    return "\n\n".join(lines)


def _search_result_to_dict(r) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """``SearchResult`` → JSON-friendly dict (与 Tool wrapper 一致)."""
    import uuid as _uuid

    return {
        "chunk_id": str(r.chunk_id) if isinstance(r.chunk_id, _uuid.UUID) else r.chunk_id,
        "doc_id": r.doc_id,
        "ipo_code": r.ipo_code,
        "page": r.page,
        "chunk_index": r.chunk_index,
        "text": r.text,
        "score": float(r.score),
        "rrf_score": float(r.rrf_score),
        "vector_rank": r.vector_rank,
        "bm25_rank": r.bm25_rank,
    }


# ─── 单条 case 三种 mode 实现 ──────────────────────────────────────────────


def _run_keyword_case(case: EvalCase) -> EvalCaseResult:
    """离线 mode: 只校验数据集自洽性 (no IO)."""
    matched: list[str] = []
    ref_lower = (case.reference_answer or "").casefold()
    for kw in case.expected_keywords:
        if kw.casefold() in ref_lower:
            matched.append(kw)
    return EvalCaseResult(
        case_id=case.id,
        category=case.category,
        mode="keyword",
        retrieved_chunks=[],
        recall_at_5_hit=None,
        matched_keywords=matched,
        elapsed_ms=0,
    )


async def _run_retrieval_case(case: EvalCase) -> EvalCaseResult:
    """retrieval mode: 跑 hybrid_search + 算 recall@5."""
    t = time.monotonic()
    factory = get_session_factory()
    try:
        async with factory() as session:
            output = await hybrid_search(
                session,
                case.query,
                ipo_code=case.ipo_code,
                doc_type="prospectus",
                final_top_k=5,
            )
        retrieved = [_search_result_to_dict(r) for r in output.results]
    except Exception as e:
        elapsed = int((time.monotonic() - t) * 1000)
        logger.warning(f"eval.retrieval.fail case={case.id}: {type(e).__name__}: {e}")
        return EvalCaseResult(
            case_id=case.id,
            category=case.category,
            mode="retrieval",
            retrieved_chunks=[],
            recall_at_5_hit=None,
            matched_keywords=[],
            error=f"{type(e).__name__}: {e}",
            elapsed_ms=elapsed,
        )

    hit, matched = compute_recall_at_5(
        retrieved,
        expected_keywords=case.expected_keywords,
        expected_doc_ids=case.expected_doc_ids,
    )
    elapsed = int((time.monotonic() - t) * 1000)
    return EvalCaseResult(
        case_id=case.id,
        category=case.category,
        mode="retrieval",
        retrieved_chunks=retrieved,
        recall_at_5_hit=hit,
        matched_keywords=matched,
        elapsed_ms=elapsed,
    )


async def _run_end_to_end_case(
    case: EvalCase,
    *,
    judge: LLMJudge | None = None,
) -> EvalCaseResult:
    """end_to_end: retrieval + LLM 直答 + hallucination + judge."""
    t = time.monotonic()
    # Step 1. retrieval
    retrieval_result = await _run_retrieval_case(case)
    if retrieval_result.error:
        # retrieval 都炸了, 没必要继续调 LLM
        return EvalCaseResult(
            case_id=case.id,
            category=case.category,
            mode="end_to_end",
            retrieved_chunks=retrieval_result.retrieved_chunks,
            recall_at_5_hit=retrieval_result.recall_at_5_hit,
            matched_keywords=retrieval_result.matched_keywords,
            error=retrieval_result.error,
            elapsed_ms=int((time.monotonic() - t) * 1000),
        )

    retrieved = retrieval_result.retrieved_chunks
    # 把 retrieved 翻译成 citation 格式 (snippet 截 200 字)
    citations: list[dict[str, Any]] = [
        {
            "idx": i + 1,
            "chunk_id": c.get("chunk_id"),
            "doc_id": c.get("doc_id"),
            "ipo_code": c.get("ipo_code"),
            "page": c.get("page"),
            "snippet": (c.get("text") or "")[:200],
            "score": c.get("score"),
        }
        for i, c in enumerate(retrieved[:5])
    ]

    # Step 2. LLM 回答
    answer_text = ""
    invalid_idx: list[int] = []
    try:
        messages = [
            {"role": "system", "content": _EVAL_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _EVAL_USER_TEMPLATE.format(
                    query=case.query.strip(),
                    context_block=_format_context(retrieved),
                ),
            },
        ]
        chat_result = await chat(
            messages,
            temperature=0.0,
            max_tokens=600,
        )
        answer_text = (chat_result.content or "").strip()
    except (LLMConfigError, LLMError) as e:
        elapsed = int((time.monotonic() - t) * 1000)
        logger.warning(f"eval.e2e.llm_fail case={case.id}: {type(e).__name__}: {e}")
        return EvalCaseResult(
            case_id=case.id,
            category=case.category,
            mode="end_to_end",
            retrieved_chunks=retrieved,
            recall_at_5_hit=retrieval_result.recall_at_5_hit,
            matched_keywords=retrieval_result.matched_keywords,
            citations=citations,
            error=f"llm_fail: {type(e).__name__}: {e}",
            elapsed_ms=elapsed,
        )

    # Step 3. 字符级幻觉 baseline + LLM judge
    halluc_score, unbacked = compute_hallucination(
        answer_text=answer_text,
        citations=citations,
        ground_truth_facts=case.ground_truth_facts,
    )

    judge_score: int | None = None
    judge_rationale = ""
    if judge is not None:
        jr = await judge.judge(
            query=case.query,
            reference_answer=case.reference_answer,
            ground_truth_facts=case.ground_truth_facts,
            answer_text=answer_text,
            citations=citations,
        )
        judge_score = jr.score
        judge_rationale = jr.rationale
        # judge 也吐 hallucinated_facts → 与字符级合并去重
        for f in jr.hallucinated_facts:
            if f and f not in unbacked:
                unbacked.append(f)

    elapsed = int((time.monotonic() - t) * 1000)
    return EvalCaseResult(
        case_id=case.id,
        category=case.category,
        mode="end_to_end",
        retrieved_chunks=retrieved,
        recall_at_5_hit=retrieval_result.recall_at_5_hit,
        matched_keywords=retrieval_result.matched_keywords,
        answer_text=answer_text,
        citations=citations,
        invalid_citation_indices=invalid_idx,
        hallucination_facts=unbacked,
        hallucination_score=halluc_score,
        judge_score=judge_score,
        judge_rationale=judge_rationale,
        elapsed_ms=elapsed,
    )


# ─── 顶层入口 ──────────────────────────────────────────────────────────────


async def run_dataset(
    *,
    dataset_path: str,
    mode: EvalMode = "retrieval",
    sprint: str = "sprint2",
    use_judge: bool = False,
    concurrency: int | None = None,
    cases_filter: list[str] | None = None,
) -> RunReport:
    """主入口: 加载数据集 → 并发跑每条 case → 拼 RunReport.

    参数
    ----
    - ``dataset_path``: JSONL 路径 (相对 cwd 或绝对)
    - ``mode``: keyword / retrieval / end_to_end
    - ``sprint``: 报告 metadata 标记, 默认 ``sprint2``
    - ``use_judge``: 仅 ``end_to_end`` 模式生效; True 则每 case 调 ``LLMJudge.judge``
    - ``concurrency``: 并发上限 (默认走 settings.eval_judge_concurrency)
    - ``cases_filter``: 仅跑指定 case_id 列表 (调试 / smoke); None = 跑全集
    """
    settings = get_settings()
    cases = load_cases(dataset_path)
    if cases_filter:
        cases_filter_set = set(cases_filter)
        cases = [c for c in cases if c.id in cases_filter_set]
        if not cases:
            raise ValueError(f"cases_filter 过滤后没有 case 可跑: {cases_filter}")

    started_at = datetime.now(tz=UTC)
    sem = asyncio.Semaphore(concurrency or settings.eval_judge_concurrency)
    judge = LLMJudge() if (mode == "end_to_end" and use_judge) else None

    async def _run_one(case: EvalCase) -> EvalCaseResult:
        async with sem:
            try:
                if mode == "keyword":
                    return _run_keyword_case(case)
                if mode == "retrieval":
                    return await _run_retrieval_case(case)
                return await _run_end_to_end_case(case, judge=judge)
            except Exception as e:
                logger.exception(f"eval.case.unexpected case={case.id}: {e}")
                return EvalCaseResult(
                    case_id=case.id,
                    category=case.category,
                    mode=mode,
                    error=f"{type(e).__name__}: {e}",
                )

    if mode == "keyword":
        # keyword 模式无 IO, 直接顺序跑省 asyncio overhead
        results = [_run_keyword_case(c) for c in cases]
    else:
        logger.info(
            f"eval.start mode={mode} sprint={sprint} cases={len(cases)} "
            f"concurrency={sem._value} judge={use_judge}"
        )
        results = await asyncio.gather(*[_run_one(c) for c in cases])

    return RunReport.new(
        sprint=sprint,
        mode=mode,
        dataset_path=str(dataset_path),
        started_at=started_at,
        cases=results,
    )


__all__ = [
    "run_dataset",
]
