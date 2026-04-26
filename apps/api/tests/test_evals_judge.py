"""``evals.judge`` 单测 (BE-S2-009).

不调真 LLM. 覆盖:
- ``LLMJudge.build_prompt``: 把 query / reference / facts / citations 塞进
  prompt 是否完整 (用 substring assert)
- ``LLMJudge.parse_response``: JSON / 含 fence / 含前后噪声 / 缺字段 / score 越界
- ``LLMJudge.judge``: monkeypatch ``app.adapters.llm_client.chat`` 模拟成功 / 失败
"""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters.llm_client import LLMProviderError
from evals.judge import JudgeResult, LLMJudge

# ─── build_prompt ─────────────────────────────────────────────────────────


def test_build_prompt_carries_all_inputs() -> None:
    judge = LLMJudge()
    msgs = judge.build_prompt(
        query="腾讯主营业务?",
        reference_answer="参考: 增值服务 / 广告 / 金融科技",
        ground_truth_facts=["事实A", "事实B"],
        answer_text="答案文本",
        citations=[
            {"idx": 1, "doc_id": "doc-a", "page": 12, "snippet": "片段 1"},
            {"idx": 2, "doc_id": "doc-b", "page": None, "snippet": "片段 2"},
        ],
    )
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    user_content = msgs[1]["content"]
    assert "腾讯主营业务?" in user_content
    assert "参考: 增值服务" in user_content
    assert "事实A" in user_content and "事实B" in user_content
    assert "答案文本" in user_content
    assert "doc-a" in user_content and "doc-b" in user_content
    assert "P12" in user_content


def test_build_prompt_handles_empty_optional_fields() -> None:
    judge = LLMJudge()
    msgs = judge.build_prompt(
        query="q",
        reference_answer="",
        ground_truth_facts=[],
        answer_text="",
        citations=[],
    )
    user_content = msgs[1]["content"]
    assert "(无)" in user_content
    assert "(空)" in user_content


# ─── parse_response ───────────────────────────────────────────────────────


def test_parse_response_clean_json() -> None:
    score, rationale, hallucinated = LLMJudge.parse_response(
        '{"score": 4, "rationale": "答案准确", "hallucinated_facts": []}'
    )
    assert score == 4
    assert rationale == "答案准确"
    assert hallucinated == []


def test_parse_response_with_fence_and_noise() -> None:
    raw = """这是 LLM 的解释:
```json
{"score": 3, "rationale": "部分有据", "hallucinated_facts": ["不在引用的数字"]}
```
还有一些尾部解释"""
    score, rationale, hallucinated = LLMJudge.parse_response(raw)
    assert score == 3
    assert "部分有据" in rationale
    assert hallucinated == ["不在引用的数字"]


def test_parse_response_missing_field_returns_none_score() -> None:
    score, rationale, _ = LLMJudge.parse_response('{"rationale": "无 score"}')
    assert score is None
    assert "无 score" in rationale


def test_parse_response_score_out_of_range_returns_none() -> None:
    score, _, _ = LLMJudge.parse_response('{"score": 7, "rationale": "x"}')
    assert score is None
    score2, _, _ = LLMJudge.parse_response('{"score": 0, "rationale": "y"}')
    assert score2 is None


def test_parse_response_invalid_json_returns_none() -> None:
    score, _, hallucinated = LLMJudge.parse_response("not a json at all")
    assert score is None
    assert hallucinated == []


def test_parse_response_empty_returns_none() -> None:
    score, rationale, hallucinated = LLMJudge.parse_response("")
    assert score is None
    assert rationale == ""
    assert hallucinated == []


def test_parse_response_score_string_coerced() -> None:
    # 容忍 LLM 偶尔给字符串 "4"
    score, _, _ = LLMJudge.parse_response('{"score": "4", "rationale": "x"}')
    assert score == 4


# ─── judge() with monkeypatched LLM client ────────────────────────────────


@pytest.mark.asyncio
async def test_judge_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ChatResultStub:
        def __init__(self, content: str) -> None:
            self.content = content

    captured: dict[str, Any] = {}

    async def _fake_chat(messages: Any, **kwargs: Any) -> _ChatResultStub:
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return _ChatResultStub(
            '{"score": 5, "rationale": "完全有据", "hallucinated_facts": []}'
        )

    monkeypatch.setattr("evals.judge.chat", _fake_chat)

    judge = LLMJudge()
    result = await judge.judge(
        query="q",
        reference_answer="ref",
        ground_truth_facts=["f1"],
        answer_text="ans",
        citations=[{"idx": 1, "doc_id": "d", "snippet": "s"}],
    )
    assert isinstance(result, JudgeResult)
    assert result.score == 5
    assert result.rationale == "完全有据"
    assert result.error == ""
    # JSON 输出强约束
    assert captured["kwargs"]["response_format"] == {"type": "json_object"}
    assert captured["kwargs"]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_judge_llm_error_returns_none_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _failing_chat(messages: Any, **kwargs: Any) -> Any:
        raise LLMProviderError("upstream 502", provider="openai", model="m", cause=None)

    monkeypatch.setattr("evals.judge.chat", _failing_chat)

    judge = LLMJudge()
    result = await judge.judge(
        query="q",
        reference_answer="r",
        ground_truth_facts=[],
        answer_text="a",
        citations=[],
    )
    assert result.score is None
    assert result.rationale == ""
    assert "LLMProviderError" in result.error


@pytest.mark.asyncio
async def test_judge_parse_failed_returns_none_with_error_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ChatResultStub:
        content = "garbage no json"

    async def _fake_chat(messages: Any, **kwargs: Any) -> _ChatResultStub:
        return _ChatResultStub()

    monkeypatch.setattr("evals.judge.chat", _fake_chat)

    judge = LLMJudge()
    result = await judge.judge(
        query="q",
        reference_answer="",
        ground_truth_facts=[],
        answer_text="a",
        citations=[],
    )
    assert result.score is None
    assert result.error == "parse_failed"
