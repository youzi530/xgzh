"""``get_sentiment_summary`` Tool 单测 (BE-S2-006b).

覆盖
====
- happy: placeholder 返回固定形状, ``ok=True`` + warning + counts=0
- 入参校验: ``code`` 长度限制, ``window_days`` 范围限制
- code 走 upper().strip() 归一
- ``window_days`` 默认 7, 可显式覆盖
- OpenAI schema 形状

策略
====
本 Tool 是纯占位, 不接 IO; 直接走 ``tool.runner(...)`` 单测即可.
"""

from __future__ import annotations

import pytest

from app.services.agent.tool_registry import get

# ─── happy ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sentiment_happy_returns_placeholder() -> None:
    tool = get("get_sentiment_summary")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK"})

    assert r.ok is True
    assert r.error is None
    assert r.data is not None
    d = r.data
    assert d["code"] == "0700.HK"
    assert d["window_days"] == 7
    assert d["counts"] == {"positive": 0, "neutral": 0, "negative": 0}
    assert d["top_articles"] == []
    assert d["data_source_status"] == "not_connected"
    assert "warning" in d
    assert "情感数据源" in d["warning"]


@pytest.mark.asyncio
async def test_sentiment_normalizes_code() -> None:
    """code 走 upper().strip(), 大小写 / 前后空格 都归一."""
    tool = get("get_sentiment_summary")
    assert tool is not None
    r = await tool.runner({"code": "  0700.hk  "})
    assert r.ok is True
    assert r.data is not None
    assert r.data["code"] == "0700.HK"


@pytest.mark.asyncio
async def test_sentiment_custom_window_days() -> None:
    tool = get("get_sentiment_summary")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK", "window_days": 14})
    assert r.ok is True
    assert r.data is not None
    assert r.data["window_days"] == 14


# ─── 入参校验 (沙盒) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sentiment_missing_code() -> None:
    tool = get("get_sentiment_summary")
    assert tool is not None
    r = await tool.runner({})
    assert r.ok is False
    assert r.error is not None
    assert "参数校验失败" in r.error


@pytest.mark.asyncio
async def test_sentiment_window_days_too_small() -> None:
    tool = get("get_sentiment_summary")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK", "window_days": 0})
    assert r.ok is False


@pytest.mark.asyncio
async def test_sentiment_window_days_too_large() -> None:
    tool = get("get_sentiment_summary")
    assert tool is not None
    r = await tool.runner({"code": "0700.HK", "window_days": 999})
    assert r.ok is False


# ─── OpenAI schema ────────────────────────────────────────────────────────


def test_sentiment_openai_schema_shape() -> None:
    tool = get("get_sentiment_summary")
    assert tool is not None
    schema = tool.to_openai_schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "get_sentiment_summary"
    # description 必须明确表达"占位 / 未接入"
    assert "placeholder" in fn["description"].lower() or "占位" in fn["description"] or "尚未接入" in fn["description"]
    params = fn["parameters"]
    assert "code" in params["properties"]
    assert "code" in params["required"]
    assert "window_days" in params["properties"]
