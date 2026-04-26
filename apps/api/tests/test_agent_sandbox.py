"""``app.services.agent.sandbox.sandboxed`` 装饰器单测 (BE-S2-006a).

覆盖
====
- happy path: 入参合法 + runner 正常返回 → ToolResult.success + elapsed_ms 被复写
- pydantic ValidationError: 类型错 / 缺字段 → ToolResult.failure 带 field 摘要
- TimeoutError: runner sleep > timeout → ToolResult.failure
- 通用 Exception: runner 抛 ValueError / RuntimeError → ToolResult.failure 带类名
- runner 不返回 ToolResult: 契约违反 → ToolResult.failure
- deps 透传: ``**deps`` 任意 kwarg 不被沙盒拦截
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from app.services.agent.sandbox import sandboxed
from app.services.agent.tool_registry import ToolResult


class _Input(BaseModel):
    code: str = Field(min_length=1)
    n: int = Field(default=1, ge=1, le=10)


# ─── happy path ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sandbox_happy_path() -> None:
    @sandboxed(input_model=_Input, timeout_seconds=2.0)
    async def runner(args: _Input) -> ToolResult:
        return ToolResult.success({"echo": args.code, "n": args.n})

    r = await runner({"code": "0700.HK", "n": 5})
    assert r.ok is True
    assert r.data == {"echo": "0700.HK", "n": 5}
    assert r.error is None
    assert r.elapsed_ms >= 0


@pytest.mark.asyncio
async def test_sandbox_default_field_via_pydantic() -> None:
    """``n`` 走 pydantic default=1, 不传时不算缺字段."""

    @sandboxed(input_model=_Input, timeout_seconds=2.0)
    async def runner(args: _Input) -> ToolResult:
        return ToolResult.success({"n": args.n})

    r = await runner({"code": "0700.HK"})
    assert r.ok is True
    assert r.data == {"n": 1}


# ─── 入参校验失败 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sandbox_validation_failure_missing_field() -> None:
    @sandboxed(input_model=_Input, timeout_seconds=2.0)
    async def runner(args: _Input) -> ToolResult:  # noqa: ARG001
        return ToolResult.success({})

    r = await runner({})
    assert r.ok is False
    assert r.error is not None
    assert "参数校验失败" in r.error
    assert "code" in r.error


@pytest.mark.asyncio
async def test_sandbox_validation_failure_type_mismatch() -> None:
    @sandboxed(input_model=_Input, timeout_seconds=2.0)
    async def runner(args: _Input) -> ToolResult:  # noqa: ARG001
        return ToolResult.success({})

    r = await runner({"code": "0700.HK", "n": "not_int"})
    assert r.ok is False
    assert r.error is not None
    assert "n" in r.error


@pytest.mark.asyncio
async def test_sandbox_validation_failure_constraint() -> None:
    @sandboxed(input_model=_Input, timeout_seconds=2.0)
    async def runner(args: _Input) -> ToolResult:  # noqa: ARG001
        return ToolResult.success({})

    r = await runner({"code": "x", "n": 100})  # n > 10
    assert r.ok is False


@pytest.mark.asyncio
async def test_sandbox_none_args_treated_as_empty_dict() -> None:
    """raw_args=None 与 raw_args={} 等价."""

    @sandboxed(input_model=_Input, timeout_seconds=2.0)
    async def runner(args: _Input) -> ToolResult:  # noqa: ARG001
        return ToolResult.success({})

    r = await runner(None)
    # code 缺 → 校验失败, 不抛异常
    assert r.ok is False


# ─── 超时 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sandbox_timeout() -> None:
    @sandboxed(input_model=_Input, timeout_seconds=0.05)
    async def runner(args: _Input) -> ToolResult:  # noqa: ARG001
        await asyncio.sleep(1.0)
        return ToolResult.success({"never": "returned"})

    r = await runner({"code": "0700.HK"})
    assert r.ok is False
    assert r.error is not None
    assert "超时" in r.error


# ─── 通用异常 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sandbox_normalizes_value_error() -> None:
    @sandboxed(input_model=_Input, timeout_seconds=2.0)
    async def runner(args: _Input) -> ToolResult:  # noqa: ARG001
        raise ValueError("upstream broke")

    r = await runner({"code": "0700.HK"})
    assert r.ok is False
    assert r.error is not None
    assert "ValueError" in r.error
    # 不应当露完整堆栈到 LLM 可读路径
    assert "upstream broke" not in r.error


@pytest.mark.asyncio
async def test_sandbox_normalizes_runtime_error() -> None:
    @sandboxed(input_model=_Input, timeout_seconds=2.0)
    async def runner(args: _Input) -> ToolResult:  # noqa: ARG001
        raise RuntimeError("db down")

    r = await runner({"code": "0700.HK"})
    assert r.ok is False
    assert r.error is not None
    assert "RuntimeError" in r.error


# ─── 契约违反 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sandbox_runner_must_return_tool_result() -> None:
    @sandboxed(input_model=_Input, timeout_seconds=2.0)
    async def runner(args: _Input) -> ToolResult:  # noqa: ARG001
        return {"not": "a tool result"}  # type: ignore[return-value]

    r = await runner({"code": "0700.HK"})
    assert r.ok is False
    assert r.error is not None
    assert "未返回 ToolResult" in r.error


# ─── deps 透传 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sandbox_passes_kwargs_to_runner() -> None:
    @sandboxed(input_model=_Input, timeout_seconds=2.0)
    async def runner(args: _Input, *, multiplier: int = 1) -> ToolResult:
        return ToolResult.success({"value": args.n * multiplier})

    r = await runner({"code": "x", "n": 3}, multiplier=10)
    assert r.ok is True
    assert r.data == {"value": 30}


# ─── elapsed_ms 被沙盒覆写 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sandbox_overrides_elapsed_ms_from_runner() -> None:
    @sandboxed(input_model=_Input, timeout_seconds=2.0)
    async def runner(args: _Input) -> ToolResult:  # noqa: ARG001
        # runner 自己声明 elapsed=999, 沙盒应该覆写为真实计时
        return ToolResult.success({"x": 1}, elapsed_ms=999)

    r = await runner({"code": "x"})
    assert r.ok is True
    # 沙盒计时应当远小于 999ms
    assert r.elapsed_ms < 999
