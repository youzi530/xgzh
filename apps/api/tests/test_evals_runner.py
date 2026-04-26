"""``evals.runner`` + ``evals.cli`` + ``evals.reporter`` 单测 (BE-S2-009).

不调真 IO. 走两条路径:
1. **keyword 模式 + 真数据集**: 全离线, 验证 80 条 case 在 keyword 模式跑通,
   生成 ``RunReport`` 字段齐全, ``render_markdown`` 渲染不抛
2. **retrieval 模式 + monkeypatched hybrid_search**: 模拟 PG 命中 / 未命中 /
   SQL 报错三种场景, 验证 runner 不让单 case 拖垮整批
3. **end_to_end 模式 + monkeypatched chat & hybrid_search**: 验证幻觉率 +
   judge 字段拼接对; LLM 抛错时不会冒泡到外层
4. **CLI exit code**: ``--fail-below-recall`` 阈值告警
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from typing import Any

import pytest

from evals.reporter import render_markdown, write_report
from evals.runner import run_dataset
from evals.schema import EvalCaseResult, RunReport

DATASET_PATH = Path(__file__).resolve().parent.parent / "evals/dataset/sprint2_80q.jsonl"


# ─── keyword mode 真数据集 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_dataset_keyword_full() -> None:
    report = await run_dataset(
        dataset_path=str(DATASET_PATH),
        mode="keyword",
        sprint="t-keyword",
    )
    assert isinstance(report, RunReport)
    assert report.summary.total == 80
    assert report.summary.failed == 0
    assert report.summary.recall_at_5 is None
    assert report.summary.hallucination_rate is None
    # 4 类齐全, 各 20 条
    assert set(report.summary.by_category.keys()) == {"basic", "risk", "peers", "rag"}
    for _cat, d in report.summary.by_category.items():
        assert d["total"] == 20
        assert d["failed"] == 0


@pytest.mark.asyncio
async def test_run_dataset_keyword_supports_cases_filter() -> None:
    report = await run_dataset(
        dataset_path=str(DATASET_PATH),
        mode="keyword",
        cases_filter=["BASIC_001", "RAG_005"],
    )
    assert report.summary.total == 2
    case_ids = {c.case_id for c in report.cases}
    assert case_ids == {"BASIC_001", "RAG_005"}


@pytest.mark.asyncio
async def test_run_dataset_cases_filter_no_match_raises() -> None:
    with pytest.raises(ValueError, match="cases_filter"):
        await run_dataset(
            dataset_path=str(DATASET_PATH),
            mode="keyword",
            cases_filter=["NO_SUCH_CASE"],
        )


# ─── retrieval mode (monkeypatch hybrid_search) ──────────────────────────


class _SearchResultStub:
    """轻量代替 ``app.services.rag.hybrid_search.SearchResult``."""

    def __init__(
        self,
        *,
        chunk_id: str = "c-1",
        doc_id: str = "doc-1",
        text: str = "",
        ipo_code: str | None = "0700.HK",
        page: int | None = 1,
        chunk_index: int | None = 0,
        score: float = 0.5,
    ) -> None:
        import uuid as _uuid

        self.chunk_id = _uuid.UUID(int=hash(chunk_id) & ((1 << 128) - 1))
        self.doc_id = doc_id
        self.text = text
        self.ipo_code = ipo_code
        self.page = page
        self.chunk_index = chunk_index
        self.score = score
        self.rrf_score = 0.05
        self.vector_rank = 1
        self.bm25_rank = 1


class _HybridSearchOutputStub:
    def __init__(self, results: list[_SearchResultStub]) -> None:
        self.results = results
        self.stats = {"vector_hits": len(results), "bm25_hits": 0}


@pytest.fixture
def stub_session_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 ``runner.get_session_factory`` 返回一个不连 DB 的 async ctx."""

    class _NullSession:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    def _factory() -> Any:
        return _NullSession

    monkeypatch.setattr("evals.runner.get_session_factory", _factory)


@pytest.mark.asyncio
async def test_run_dataset_retrieval_mode_with_mocked_hits(
    monkeypatch: pytest.MonkeyPatch, stub_session_factory: None
) -> None:
    async def _mock_hybrid_search(session: Any, query: str, **kwargs: Any) -> Any:
        return _HybridSearchOutputStub(
            results=[
                _SearchResultStub(
                    text="腾讯控股 2004 年 6 月 16 日在港交所主板上市, 主营业务包括"
                    " 增值服务 (社交、游戏)、网络广告、金融科技及企业服务"
                ),
                _SearchResultStub(text="无关 chunk"),
            ]
        )

    monkeypatch.setattr("evals.runner.hybrid_search", _mock_hybrid_search)

    report = await run_dataset(
        dataset_path=str(DATASET_PATH),
        mode="retrieval",
        cases_filter=["BASIC_001"],
        concurrency=1,
    )
    assert report.summary.total == 1
    assert report.summary.recall_at_5 == 1.0
    case = report.cases[0]
    assert case.recall_at_5_hit is True
    assert "主营业务" in case.matched_keywords or "增值服务" in case.matched_keywords


@pytest.mark.asyncio
async def test_run_dataset_retrieval_mode_no_hit(
    monkeypatch: pytest.MonkeyPatch, stub_session_factory: None
) -> None:
    async def _mock_no_hit(session: Any, query: str, **kwargs: Any) -> Any:
        return _HybridSearchOutputStub(results=[_SearchResultStub(text="完全无关")])

    monkeypatch.setattr("evals.runner.hybrid_search", _mock_no_hit)

    report = await run_dataset(
        dataset_path=str(DATASET_PATH),
        mode="retrieval",
        cases_filter=["BASIC_001"],
        concurrency=1,
    )
    assert report.summary.recall_at_5 == 0.0
    assert report.cases[0].recall_at_5_hit is False


@pytest.mark.asyncio
async def test_run_dataset_retrieval_db_error_isolated(
    monkeypatch: pytest.MonkeyPatch, stub_session_factory: None
) -> None:
    async def _boom(session: Any, query: str, **kwargs: Any) -> Any:
        raise RuntimeError("PG down")

    monkeypatch.setattr("evals.runner.hybrid_search", _boom)

    report = await run_dataset(
        dataset_path=str(DATASET_PATH),
        mode="retrieval",
        cases_filter=["BASIC_001", "BASIC_002"],
        concurrency=2,
    )
    assert report.summary.total == 2
    assert report.summary.failed == 2
    for c in report.cases:
        assert "PG down" in c.error


# ─── end_to_end mode (monkeypatch hybrid_search + chat) ──────────────────


@pytest.mark.asyncio
async def test_run_dataset_end_to_end_metrics(
    monkeypatch: pytest.MonkeyPatch, stub_session_factory: None
) -> None:
    async def _mock_search(session: Any, query: str, **kwargs: Any) -> Any:
        # BASIC_001 expected_keywords 含 "主营业务" / "增值服务" / "广告" 等;
        # chunk 必须能命中其中之一才让 recall_at_5_hit=True
        return _HybridSearchOutputStub(
            results=[
                _SearchResultStub(
                    text=(
                        "腾讯控股 2004 年 6 月 16 日 在港交所主板上市, 主营业务包括 "
                        "增值服务 (社交 / 游戏)、网络广告、金融科技及企业服务"
                    )
                )
            ]
        )

    class _ChatResultStub:
        content = "腾讯主营业务包括 2004 年 6 月 16 日 起的增值服务、广告。[1]"
        finish_reason = "stop"
        tool_calls = None

    async def _mock_chat(messages: Any, **kwargs: Any) -> _ChatResultStub:
        return _ChatResultStub()

    monkeypatch.setattr("evals.runner.hybrid_search", _mock_search)
    monkeypatch.setattr("evals.runner.chat", _mock_chat)

    report = await run_dataset(
        dataset_path=str(DATASET_PATH),
        mode="end_to_end",
        cases_filter=["BASIC_001"],
        concurrency=1,
    )
    assert report.summary.total == 1
    case = report.cases[0]
    assert case.error == ""
    assert case.recall_at_5_hit is True
    # 字符级 hallucination: 答案中的 "2004 年 6 月 16 日" 在 citation 中应能找到 → 0.0
    assert case.hallucination_score == 0.0
    assert case.judge_score is None  # 没开 use_judge


@pytest.mark.asyncio
async def test_run_dataset_end_to_end_llm_error_does_not_crash(
    monkeypatch: pytest.MonkeyPatch, stub_session_factory: None
) -> None:
    async def _mock_search(session: Any, query: str, **kwargs: Any) -> Any:
        return _HybridSearchOutputStub(results=[_SearchResultStub(text="anything")])

    async def _failing_chat(messages: Any, **kwargs: Any) -> Any:
        from app.adapters.llm_client import LLMConfigError

        raise LLMConfigError("no api key")

    monkeypatch.setattr("evals.runner.hybrid_search", _mock_search)
    monkeypatch.setattr("evals.runner.chat", _failing_chat)

    report = await run_dataset(
        dataset_path=str(DATASET_PATH),
        mode="end_to_end",
        cases_filter=["BASIC_001"],
        concurrency=1,
    )
    case = report.cases[0]
    assert "llm_fail" in case.error
    # 但 retrieval 已经做完, 字段不丢
    assert case.retrieved_chunks


# ─── reporter ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_render_markdown_does_not_throw_on_keyword() -> None:
    report = await run_dataset(
        dataset_path=str(DATASET_PATH), mode="keyword", cases_filter=["BASIC_001"]
    )
    md = render_markdown(report)
    assert "XGZH 离线评测报告" in md
    assert "评测模式" in md
    assert "n/a" in md  # keyword mode 指标全 None
    assert "BASIC_001" not in md or "失败 case" in md  # 此 case 不属于失败


def test_write_report_creates_files(tmp_path: Path) -> None:
    from datetime import datetime

    from evals.schema import RunReport

    report = RunReport.new(
        sprint="unit",
        mode="keyword",
        dataset_path="dataset.jsonl",
        started_at=datetime.now(tz=UTC),
        cases=[
            EvalCaseResult(
                case_id="X1", category="basic", mode="keyword"
            )
        ],
    )
    json_path, md_path = write_report(report, out_dir=tmp_path)
    assert json_path.exists() and json_path.suffix == ".json"
    assert md_path.exists() and md_path.suffix == ".md"
    md = md_path.read_text(encoding="utf-8")
    assert "unit" in md


# ─── CLI exit code ────────────────────────────────────────────────────────


def test_cli_keyword_no_write_exit_zero(tmp_path: Path) -> None:
    """``python -m evals.cli --mode keyword --no-write`` 应 exit code 0."""
    from evals.cli import main

    rc = main(
        [
            "--mode",
            "keyword",
            "--no-write",
            "--dataset",
            str(DATASET_PATH),
            "--cases",
            "BASIC_001,RAG_005",
        ]
    )
    assert rc == 0


def test_cli_dataset_missing_exits_one(tmp_path: Path) -> None:
    from evals.cli import main

    rc = main(["--mode", "keyword", "--no-write", "--dataset", "/no/such/dataset.jsonl"])
    assert rc == 1
