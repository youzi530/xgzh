"""``get_financial_statements`` Tool 单测 (BE-S2-006a).

覆盖
====
- happy: 含 financial_summary / highlights / risks → ToolResult.success
- 缺 financial_summary: data 携带 ``financial_summary=None`` + warning, 仍 ``ok=True``
- not_found: get_ipo_detail 返回 None → failure
- 入参校验: years 越界 (< 1 / > 5) / code 缺失
- years 透传: data["years_requested"] 反映入参
- highlights / risks 类型异常兜底为 []
- 上游异常归一
- OpenAI schema 形状

策略
====
``ipo_service.get_ipo_detail`` 是 @cached 装饰过的, 直接 monkeypatch 替换整个
属性即可, 缓存逻辑不会拦截 (因为我们替换的是符号绑定, 缓存层在原函数上).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import ipo_service
from app.services.agent.tool_registry import get


def _make_detail(
    *,
    code: str = "0700.HK",
    market: str = "HK",
    financial_summary: dict[str, Any] | None = None,
    highlights: list[str] | None = None,
    risks: list[str] | None = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "code": code,
        "name": "腾讯控股",
        "market": market,
        "industry": "互联网",
        "issue_price": 3.70,
        "issue_currency": "HKD",
        "listing_date": "2004-06-16",
        "raised_amount": 1.2e10,
        "pe_ratio": 15.5,
        "highlights": highlights if highlights is not None else [],
        "risks": risks if risks is not None else [],
        "financial_summary": financial_summary,
    }
    if extras:
        base.update(extras)
    return base


# ─── happy ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_financial_happy_with_financial_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    fin = {
        "revenue_3y": [100.0, 200.0, 300.0],
        "net_profit_3y": [10.0, 30.0, 60.0],
        "gross_margin_3y": [0.4, 0.42, 0.45],
    }

    async def fake_detail(code: str) -> dict[str, Any] | None:
        assert code == "0700.HK"
        return _make_detail(
            financial_summary=fin,
            highlights=["业绩稳健", "用户基数大"],
            risks=["监管风险"],
        )

    monkeypatch.setattr(ipo_service, "get_ipo_detail", fake_detail)

    tool = get("get_financial_statements")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK", "years": 3})

    assert r.ok is True
    assert r.data is not None
    d = r.data
    assert d["code"] == "0700.HK"
    assert d["market"] == "HK"
    assert d["financial_summary"] == fin
    assert d["highlights"] == ["业绩稳健", "用户基数大"]
    assert d["risks"] == ["监管风险"]
    assert d["years_requested"] == 3
    assert "warning" not in d


# ─── financial_summary 缺失 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_financial_missing_summary_returns_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_detail(code: str) -> dict[str, Any] | None:  # noqa: ARG001
        return _make_detail(financial_summary=None, highlights=[], risks=[])

    monkeypatch.setattr(ipo_service, "get_ipo_detail", fake_detail)

    tool = get("get_financial_statements")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK"})

    assert r.ok is True  # 数据缺失不算调用失败
    assert r.data is not None
    assert r.data["financial_summary"] is None
    assert "warning" in r.data
    assert "hybrid_search" in r.data["warning"]


@pytest.mark.asyncio
async def test_financial_summary_wrong_type_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧数据 / 异常写入时 financial_summary 是字符串, 兜底为 None."""

    async def fake_detail(code: str) -> dict[str, Any] | None:  # noqa: ARG001
        return _make_detail(financial_summary="not a dict")  # type: ignore[arg-type]

    monkeypatch.setattr(ipo_service, "get_ipo_detail", fake_detail)

    tool = get("get_financial_statements")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK"})
    assert r.ok is True
    assert r.data is not None
    assert r.data["financial_summary"] is None


@pytest.mark.asyncio
async def test_financial_highlights_risks_non_list_treated_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_detail(code: str) -> dict[str, Any] | None:  # noqa: ARG001
        d = _make_detail()
        d["highlights"] = "not a list"  # type: ignore[assignment]
        d["risks"] = None
        return d

    monkeypatch.setattr(ipo_service, "get_ipo_detail", fake_detail)

    tool = get("get_financial_statements")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK"})
    assert r.ok is True
    assert r.data is not None
    assert r.data["highlights"] == []
    assert r.data["risks"] == []


# ─── not_found ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_financial_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_detail(code: str) -> dict[str, Any] | None:  # noqa: ARG001
        return None

    monkeypatch.setattr(ipo_service, "get_ipo_detail", fake_detail)

    tool = get("get_financial_statements")
    assert tool is not None
    r = await tool.runner({"code": "9999.HK"})
    assert r.ok is False
    assert r.error is not None
    assert "未找到" in r.error


# ─── 入参校验 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_financial_validation_years_too_small() -> None:
    tool = get("get_financial_statements")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK", "years": 0})
    assert r.ok is False
    assert r.error is not None
    assert "years" in r.error


@pytest.mark.asyncio
async def test_financial_validation_years_too_large() -> None:
    tool = get("get_financial_statements")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK", "years": 10})
    assert r.ok is False


@pytest.mark.asyncio
async def test_financial_validation_missing_code() -> None:
    tool = get("get_financial_statements")
    assert tool is not None
    r = await tool.runner({})
    assert r.ok is False
    assert r.error is not None
    assert "code" in r.error


# ─── years 透传 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_financial_years_default(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_detail(code: str) -> dict[str, Any] | None:  # noqa: ARG001
        return _make_detail(financial_summary={"x": 1})

    monkeypatch.setattr(ipo_service, "get_ipo_detail", fake_detail)

    tool = get("get_financial_statements")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK"})  # years 不传
    assert r.ok is True
    assert r.data is not None
    assert r.data["years_requested"] == 3  # default


# ─── 上游异常归一 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_financial_upstream_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_detail(code: str) -> dict[str, Any] | None:  # noqa: ARG001
        raise RuntimeError("redis down")

    monkeypatch.setattr(ipo_service, "get_ipo_detail", fake_detail)

    tool = get("get_financial_statements")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK"})
    assert r.ok is False
    assert r.error is not None
    assert "RuntimeError" in r.error


# ─── OpenAI schema ───────────────────────────────────────────────────────


def test_financial_openai_schema_shape() -> None:
    tool = get("get_financial_statements")
    assert tool is not None
    schema = tool.to_openai_schema()
    fn = schema["function"]
    assert fn["name"] == "get_financial_statements"
    params = fn["parameters"]
    assert "code" in params["properties"]
    assert "years" in params["properties"]
    assert "code" in params["required"]
    # years 有 default → 不在 required 列
    assert "years" not in params.get("required", [])
