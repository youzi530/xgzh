"""``app.services.agent.tool_registry`` 单测 (BE-S2-006a).

覆盖
====
- ``Tool.to_openai_schema()`` 形状对齐 OpenAI ``tools=[{type: function, ...}]``
- ``register`` happy / 重名替换 + warning / name 不合法 / runner 非 callable /
  input_model 非 BaseModel
- ``get`` / ``list_all`` / ``list_openai_schemas`` / ``unregister``
- ``ToolResult.success`` / ``ToolResult.failure``
- 测试隔离 fixture: 每条用例前/后用 ``clear_registry_for_test()`` + 重 import
  ``tools`` 子包恢复 2 个默认 Tool, 不让单测互相串

为什么要重 import ``tools`` 子包
================================
``tools/__init__.py`` 走 module side effect 注册. import 一次后 Python 把模块
缓存进 ``sys.modules``, 第二次 import 时**不会再执行 register()**. 所以 fixture
里 ``importlib.reload(tools)`` 强制重跑模块顶层逻辑, 让 ``clear_registry_for_test``
后能恢复初始的 2 Tool 状态.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest
from pydantic import BaseModel, Field

from app.services.agent import tool_registry
from app.services.agent import tools as tools_pkg
from app.services.agent.tool_registry import (
    Tool,
    ToolResult,
    clear_registry_for_test,
    get,
    list_all,
    list_openai_schemas,
    register,
    unregister,
)


@pytest.fixture(autouse=True)
def _restore_default_tools() -> Iterator[None]:
    """每条用例前后清干净 + 重灌 2 个默认 Tool."""
    clear_registry_for_test()
    importlib.reload(tools_pkg.basic_info)
    importlib.reload(tools_pkg.financial)
    yield
    clear_registry_for_test()
    importlib.reload(tools_pkg.basic_info)
    importlib.reload(tools_pkg.financial)


# ─── ToolResult ────────────────────────────────────────────────────────────


def test_tool_result_success_factory() -> None:
    r = ToolResult.success({"x": 1}, elapsed_ms=42)
    assert r.ok is True
    assert r.data == {"x": 1}
    assert r.error is None
    assert r.elapsed_ms == 42


def test_tool_result_failure_factory() -> None:
    r = ToolResult.failure("boom", elapsed_ms=7)
    assert r.ok is False
    assert r.data is None
    assert r.error == "boom"
    assert r.elapsed_ms == 7


def test_tool_result_is_frozen() -> None:
    r = ToolResult.success({"x": 1})
    with pytest.raises(Exception):  # noqa: B017, PT011
        r.ok = False  # type: ignore[misc]


# ─── Tool.to_openai_schema ────────────────────────────────────────────────


class _DummyInput(BaseModel):
    code: str = Field(description="some code")
    n: int = Field(default=3, ge=1)


async def _dummy_runner(args: _DummyInput) -> ToolResult:  # noqa: ARG001
    return ToolResult.success({})


def test_to_openai_schema_shape() -> None:
    t = Tool(
        name="dummy_tool",
        description="dummy",
        input_model=_DummyInput,
        runner=_dummy_runner,
    )
    schema = t.to_openai_schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "dummy_tool"
    assert fn["description"] == "dummy"
    params = fn["parameters"]
    # JSON schema 必须有 properties + 至少 required code
    assert "properties" in params
    assert "code" in params["properties"]
    # title 已被 strip 掉
    assert "title" not in params


# ─── register / get / list_all / unregister ───────────────────────────────


def test_register_then_get_returns_same_tool() -> None:
    t = Tool(
        name="dummy_tool",
        description="dummy",
        input_model=_DummyInput,
        runner=_dummy_runner,
    )
    register(t)
    assert get("dummy_tool") is t


def test_get_unknown_returns_none() -> None:
    assert get("not_exist_tool_xyz") is None


def test_list_all_returns_sorted_by_name() -> None:
    # default 已有 get_financial_statements + get_ipo_basic_info
    t = Tool(
        name="aaa_tool",
        description="aa",
        input_model=_DummyInput,
        runner=_dummy_runner,
    )
    register(t)
    names = [x.name for x in list_all()]
    assert names == sorted(names), "list_all 必须按 name 字典序"
    assert "aaa_tool" in names


def test_list_openai_schemas_includes_all_registered() -> None:
    schemas = list_openai_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert "get_ipo_basic_info" in names
    assert "get_financial_statements" in names


def test_register_duplicate_name_replaces_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """loguru 不通过 stdlib logging, 直接 patch logger.warning 抓 message."""
    warnings: list[str] = []

    from app.services.agent import tool_registry as registry_mod

    def _capture_warn(msg: str, *args: object, **kwargs: object) -> None:  # noqa: ARG001
        warnings.append(msg)

    monkeypatch.setattr(registry_mod.logger, "warning", _capture_warn)

    t1 = Tool("dummy", "d1", _DummyInput, _dummy_runner)
    t2 = Tool("dummy", "d2", _DummyInput, _dummy_runner)
    register(t1)
    register(t2)
    assert get("dummy") is t2
    assert any("tool_registry.replace" in m for m in warnings)


def test_register_invalid_name_raises() -> None:
    with pytest.raises(ValueError, match="不合法"):
        register(Tool("", "x", _DummyInput, _dummy_runner))
    with pytest.raises(ValueError, match="不合法"):
        register(Tool("with space", "x", _DummyInput, _dummy_runner))
    with pytest.raises(ValueError, match="不合法"):
        register(Tool("a" * 65, "x", _DummyInput, _dummy_runner))


def test_register_non_callable_runner_raises() -> None:
    with pytest.raises(ValueError, match="callable"):
        register(Tool("dummy", "x", _DummyInput, "not_callable"))  # type: ignore[arg-type]


def test_register_input_model_must_be_basemodel() -> None:
    class _NotBaseModel:
        pass

    with pytest.raises(ValueError, match="BaseModel"):
        register(
            Tool("dummy", "x", _NotBaseModel, _dummy_runner)  # type: ignore[arg-type]
        )


def test_unregister_removes() -> None:
    register(Tool("dummy", "x", _DummyInput, _dummy_runner))
    assert unregister("dummy") is True
    assert get("dummy") is None
    assert unregister("dummy") is False  # 二次 unreg 返回 False


# ─── 默认两 Tool 已通过模块 side effect 注册 ─────────────────────────────


def test_default_tools_auto_registered() -> None:
    """import ``app.services.agent.tools`` 触发 2 个默认 Tool 自动注册."""
    assert get("get_ipo_basic_info") is not None
    assert get("get_financial_statements") is not None


def test_clear_registry_for_test_helper() -> None:
    clear_registry_for_test()
    assert list_all() == []
    # fixture teardown 会重灌, 这里不污染下条用例


# 让 ruff 别报 tool_registry 未用
_ = tool_registry
