"""``services/agent/system_prompt.py`` 单测 (BE-S2-007).

覆盖
====
- 基础 prompt 含红线 / 输出格式 / 引用编号约定
- 动态 Tool 列表自动注入
- ``ipo_code`` 注入"会话锚点"片段
"""

from __future__ import annotations

from app.services.agent import tools as _tools_pkg  # noqa: F401  (side-effect register)
from app.services.agent.system_prompt import build_system_prompt
from app.services.agent.tool_registry import list_all


def test_build_system_prompt_contains_red_lines() -> None:
    prompt = build_system_prompt()
    assert "数据真实性" in prompt
    assert "中立性" in prompt
    assert "不构成投资建议" in prompt


def test_build_system_prompt_lists_registered_tools() -> None:
    prompt = build_system_prompt()
    names = [t.name for t in list_all()]
    assert names, "BE-S2-006a/b 应已注册若干 Tool"
    for name in names:
        assert f"`{name}`" in prompt, f"system prompt 缺少 Tool {name}"


def test_build_system_prompt_with_ipo_anchor() -> None:
    prompt = build_system_prompt(ipo_code="0700.HK")
    assert "0700.HK" in prompt
    assert "会话锚点" in prompt


def test_build_system_prompt_without_ipo_anchor_omitted() -> None:
    prompt = build_system_prompt()
    assert "会话锚点" not in prompt


def test_build_system_prompt_citation_convention_present() -> None:
    prompt = build_system_prompt()
    assert "[1]" in prompt
    assert "引用编号约定" in prompt
    assert "hybrid_search" in prompt
