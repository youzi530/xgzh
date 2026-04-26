"""LLM-as-judge: 用 LLM 给 Agent 答案打分 (1-5).

为什么要 LLM-as-judge
=====================
spec/04 §2.5 要求 "由 GPT-4o/Claude 作为 Judge 评估是否引用正确"; 字符级 baseline
``metrics.compute_hallucination`` 只抓数字 / 日期 / 金额, 抓不住语义幻觉
("公司有 3 大业务" 写成 "公司有 5 大业务" 但 5 没出现在 atomic_facts).

设计选择
========
1. **强制 JSON 输出**: 走 OpenAI ``response_format={"type": "json_object"}`` 让
   LLM 直接吐 ``{"score": int, "rationale": str, "hallucinated_facts": [...]}``,
   不解析自然语言, 防"格式漂移"
2. **裁判模型独立可配**: ``settings.eval_judge_model`` 默认与
   ``llm_primary_model`` 一致 (省成本); 真要拉高质量改 .env 单独走 GPT-4o /
   Claude-Opus 也支持 (走 LiteLLM 路由统一)
3. **打 1-5 分**: spec 没强约束分数区间, 选 1-5 (奇数避免"中间分数怎么投"; 与
   产品惯用 NPS 5 分制对齐)
4. **失败回退到 None**: judge 失败 (LLM 抖动 / JSON parse 失败 / 超时) 不让整批
   评测炸; 该 case ``judge_score=None``, ``RunSummary`` 算 mean 时排除
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.adapters.llm_client import LLMConfigError, LLMProviderError, chat
from app.core.config import get_settings
from app.core.logging import logger

_JUDGE_SYSTEM_PROMPT = """你是一名严苛的金融问答评估专家。你的任务是评估 AI 助手回答用户关于港股 / A 股 IPO 提问的质量。

评分维度 (1-5 分, 整数):
- **5 分**: 答案准确、引用充分; 数字 / 日期 / 关键事实全部能在所给 citations 中找到对应; 与 reference_answer 不冲突
- **4 分**: 答案大致准确但有 1-2 处轻微偏差或遗漏; 引用基本到位; 没有编造关键事实
- **3 分**: 答案部分正确; 有 1-2 个事实未引用或与 reference 有出入; 不至于误导用户
- **2 分**: 答案大量错误 / 编造事实 / 严重缺失关键引用; 用户读完会有错误印象
- **1 分**: 答案完全偏题 / 编造关键事实 / 与 reference 严重冲突, 不能给用户

如发现答案中的具体事实 (数字 / 日期 / 名称等) 在 citations 中无法找到证据, 必须把它列入
``hallucinated_facts`` 数组. 如果一切都有据, ``hallucinated_facts`` 留空数组.

只输出 JSON, 不要任何其他解释:
{
  "score": <1-5 整数>,
  "rationale": "<不超过 150 字的中文评分理由>",
  "hallucinated_facts": ["<幻觉事实 1>", ...]
}
"""

_JUDGE_USER_TEMPLATE = """## 用户问题
{query}

## 参考答案 (人工标注; 仅供参考, 答案不必逐字一致)
{reference_answer}

## 必须覆盖的关键事实 (ground_truth_facts)
{ground_truth_facts}

## AI 助手的回答
{answer_text}

## 答案引用的来源 (citations)
{citations_block}

请按照系统提示给出 JSON 评分。"""


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """``LLMJudge.judge`` 的返回. score=None 表示 LLM 调用 / parse 失败."""

    score: int | None
    rationale: str
    hallucinated_facts: list[str]
    elapsed_ms: int = 0
    error: str = ""


class LLMJudge:
    """LLM-as-judge 调度器, 持有 model + max_tokens + 超时配置."""

    def __init__(
        self,
        *,
        model: str | None = None,
        max_tokens: int = 600,
    ) -> None:
        s = get_settings()
        self.model = model or s.eval_judge_model
        self.max_tokens = max_tokens
        self.fallback_model = s.llm_primary_model

    def _format_citations(self, citations: list[dict[str, Any]]) -> str:
        if not citations:
            return "(无)"
        lines: list[str] = []
        for c in citations[:10]:
            idx = c.get("idx")
            doc_id = c.get("doc_id", "?")
            page = c.get("page")
            snippet = (c.get("snippet") or c.get("text") or "").strip()
            if len(snippet) > 240:
                snippet = snippet[:240] + "…"
            page_part = f" P{page}" if page else ""
            lines.append(f"[{idx}] {doc_id}{page_part}: {snippet}")
        return "\n".join(lines)

    def _format_facts(self, facts: list[str]) -> str:
        if not facts:
            return "(无)"
        return "\n".join(f"- {f}" for f in facts)

    def build_prompt(
        self,
        *,
        query: str,
        reference_answer: str,
        ground_truth_facts: list[str],
        answer_text: str,
        citations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """暴露 prompt 装配给单测 (不调 LLM 也能断言 prompt 拼接对)."""
        user_content = _JUDGE_USER_TEMPLATE.format(
            query=query.strip(),
            reference_answer=reference_answer.strip() or "(无)",
            ground_truth_facts=self._format_facts(ground_truth_facts),
            answer_text=answer_text.strip() or "(空)",
            citations_block=self._format_citations(citations),
        )
        return [
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    @staticmethod
    def parse_response(content: str) -> tuple[int | None, str, list[str]]:
        """从裁判 LLM 的 JSON 输出里解析 (score, rationale, hallucinated_facts).

        鲁棒性: LLM 偶尔会塞 ```json ... ``` fence, 或者前后多句解释; 这里抓第一段
        ``{...}`` 解析, 不强校验 LLM 完全干净.
        """
        if not content:
            return None, "", []
        # 抓第一段 JSON object
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None, content[:200], []
        raw_json = content[start : end + 1]
        try:
            obj = json.loads(raw_json)
        except json.JSONDecodeError:
            return None, content[:200], []
        score_raw = obj.get("score")
        try:
            score = int(score_raw) if score_raw is not None else None
        except (TypeError, ValueError):
            score = None
        if score is not None and not (1 <= score <= 5):
            score = None
        rationale = str(obj.get("rationale", "") or "")[:600]
        hallucinated = obj.get("hallucinated_facts") or []
        if not isinstance(hallucinated, list):
            hallucinated = []
        hallucinated_facts = [str(f) for f in hallucinated if f]
        return score, rationale, hallucinated_facts

    async def judge(
        self,
        *,
        query: str,
        reference_answer: str,
        ground_truth_facts: list[str],
        answer_text: str,
        citations: list[dict[str, Any]],
    ) -> JudgeResult:
        """跑一次 LLM 评分. 失败返回 ``score=None`` + ``error=...``."""
        import time

        messages = self.build_prompt(
            query=query,
            reference_answer=reference_answer,
            ground_truth_facts=ground_truth_facts,
            answer_text=answer_text,
            citations=citations,
        )
        t = time.monotonic()
        try:
            result = await chat(
                messages,
                model=self.model,
                temperature=0.0,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )
        except (LLMConfigError, LLMProviderError) as e:
            elapsed = int((time.monotonic() - t) * 1000)
            logger.warning(f"eval.judge.llm_fail model={self.model}: {e}")
            return JudgeResult(
                score=None,
                rationale="",
                hallucinated_facts=[],
                elapsed_ms=elapsed,
                error=f"{type(e).__name__}: {e}",
            )

        elapsed = int((time.monotonic() - t) * 1000)
        score, rationale, hallucinated = self.parse_response(result.content)
        if score is None:
            return JudgeResult(
                score=None,
                rationale=rationale or "judge JSON parse failed",
                hallucinated_facts=[],
                elapsed_ms=elapsed,
                error="parse_failed",
            )
        return JudgeResult(
            score=score,
            rationale=rationale,
            hallucinated_facts=hallucinated,
            elapsed_ms=elapsed,
        )


__all__ = ["JudgeResult", "LLMJudge"]
