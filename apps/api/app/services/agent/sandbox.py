"""Tool 沙盒 (BE-S2-006a Tool Use 第 2 层).

职责
====
1. **超时**: ``asyncio.wait_for`` 限单 Tool 调用时长, 防上游慢 / 死锁把 ReAct 主
   循环卡死
2. **入参校验**: pydantic ``input_model.model_validate(args)``; LLM tool_call
   参数 dict 进来即校验, 校验失败归一 ``ToolResult.failure``
3. **异常归一**: Tool runner 内部任何 exception (上游 HTTP 错 / DB 错 / 业务断言)
   统一吞掉, ``logger.exception`` 记一次 + 返回 ``ToolResult.failure``;
   **绝不** 让 unhandled exception 把 BE-S2-007 LangGraph 链路打断
4. **耗时统计**: 写入 ``ToolResult.elapsed_ms``; BE-S2-007 入 ``chat_tool_calls``
   时直接复用

使用方式
========
```python
from app.services.agent.sandbox import sandboxed

class MyToolInput(BaseModel):
    code: str

@sandboxed(input_model=MyToolInput, timeout_seconds=5.0)
async def _run(args: MyToolInput) -> ToolResult:
    # 这里只写正常路径; 异常 / 校验 / 超时 sandbox 全包了
    data = await some_io(args.code)
    return ToolResult.success({"data": data})
```

为什么走装饰器而非"在 ``Tool.runner`` 外层硬包一层"
======================================================
- 让每个 Tool 文件**自包含**: 看 Tool 实现就能知道它的 input model + 超时,
  不用跳到注册中心或主循环找
- 装饰器是"实现细节", 注册中心 ``Tool.runner`` 类型签名仍只是 ``ToolRunner``;
  BE-S2-007 主循环不感知是否被装饰过
- ``Tool.timeout_seconds`` metadata 与 ``@sandboxed(timeout=...)`` 必须一致 (这里
  做了 dataclass 冗余存储一份), 让 BE-S2-007 调度时也能感知 (例如 step 总预算
  分配)

不在本文件做
============
- LLM tool_call 反序列化 / OpenAI schema 解析 (注册中心已做)
- 副作用 audit log / billing 计费 (BE-S2-007 主循环 + BE-S2-008 配额)
- 跨进程隔离 (生产部署级, 当前 IPO 表读 + RAG 检索都在同一进程内, 不需要)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.logging import logger
from app.services.agent.tool_registry import ToolResult


def sandboxed(
    *,
    input_model: type[BaseModel],
    timeout_seconds: float = 10.0,
) -> Callable[
    [Callable[..., Awaitable[ToolResult]]], Callable[..., Awaitable[ToolResult]]
]:
    """装饰一个 Tool runner, 加上超时 + 入参校验 + 异常归一.

    runner 签名 (装饰器内部)
    -------------------------
    - 装饰前: ``async def runner(args: input_model_instance, **deps) -> ToolResult``
    - 装饰后: ``async def runner(raw_args: dict, **deps) -> ToolResult``;
      内部把 dict 转 ``input_model`` 实例再调原函数

    deps 透传
    ---------
    任何 ``**deps`` (例如 ``session: AsyncSession``) 透传给原 runner;
    BE-S2-007 主循环把 session / settings 注入时不被沙盒拦截.
    """

    def decorator(
        fn: Callable[..., Awaitable[ToolResult]],
    ) -> Callable[..., Awaitable[ToolResult]]:
        @wraps(fn)
        async def wrapper(
            raw_args: dict[str, Any] | None = None,
            **deps: Any,
        ) -> ToolResult:
            t0 = time.monotonic()
            raw_args = raw_args or {}

            # 1. 入参校验
            try:
                args = input_model.model_validate(raw_args)
            except ValidationError as e:
                # 只露 field-level 摘要, 不露完整 schema (避免 LLM 学习反向越权)
                fields = ", ".join(
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in e.errors()
                )
                elapsed = int((time.monotonic() - t0) * 1000)
                logger.info(
                    f"tool.sandbox.validation_failed fn={fn.__qualname__} "
                    f"fields=[{fields}]"
                )
                return ToolResult.failure(
                    f"参数校验失败: {fields}", elapsed_ms=elapsed
                )

            # 2. 超时 + 异常归一
            try:
                result = await asyncio.wait_for(
                    fn(args, **deps), timeout=timeout_seconds
                )
            except TimeoutError:
                elapsed = int((time.monotonic() - t0) * 1000)
                logger.warning(
                    f"tool.sandbox.timeout fn={fn.__qualname__} "
                    f"timeout={timeout_seconds}s elapsed={elapsed}ms"
                )
                return ToolResult.failure(
                    f"工具调用超时 (>{timeout_seconds}s)", elapsed_ms=elapsed
                )
            except Exception as e:
                elapsed = int((time.monotonic() - t0) * 1000)
                logger.exception(f"tool.sandbox.unhandled fn={fn.__qualname__}: {e}")
                return ToolResult.failure(
                    f"工具内部错误: {e.__class__.__name__}", elapsed_ms=elapsed
                )

            # 3. 走到这里 fn 返回了 ToolResult; 复写 elapsed_ms
            #    (让 fn 内部不必关心计时; 计时统一在沙盒侧)
            elapsed = int((time.monotonic() - t0) * 1000)
            if isinstance(result, ToolResult):
                # 不可变 dataclass → 走 dataclasses.replace 风格
                return ToolResult(
                    ok=result.ok,
                    data=result.data,
                    error=result.error,
                    elapsed_ms=elapsed,
                )

            # fn 没返回 ToolResult? 严重契约违反, 归一为失败
            logger.error(
                f"tool.sandbox.contract_violation fn={fn.__qualname__} "
                f"return_type={type(result).__name__}"
            )
            return ToolResult.failure(
                "工具实现错误: runner 未返回 ToolResult", elapsed_ms=elapsed
            )

        return wrapper

    return decorator


__all__ = ["sandboxed"]
