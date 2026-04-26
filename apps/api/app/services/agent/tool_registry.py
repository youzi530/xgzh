"""Tool 注册中心 (BE-S2-006a Tool Use 第 1 层).

设计目标
========
1. 给 BE-S2-007 LangGraph 主循环一个统一接口拿"全部可用 Tool" + "OpenAI tools
   schema 列表", 不用主循环手动维护字符串列表
2. Tool 协议走 OpenAI ``tools=[{"type": "function", "function": {...}}]`` 格式;
   DeepSeek-V3 / Qwen / 智谱 GLM-4 都兼容
3. Tool 入参用 pydantic ``BaseModel`` 描述, ``model_json_schema()`` 自动产 JSON
   schema 注入到 ``function.parameters``, 一处定义 = 入参文档 + 入参校验 + LLM
   schema 三合一
4. 模块级 ``_REGISTRY`` 单例, 保证 ``register()`` 是显式 idempotent (重复注册同名
   不抛, 但替换实现并 logger.warning), 让 hot reload / unit test 重导包时不爆

为什么不上 LangChain ``BaseTool`` / ``StructuredTool``
=====================================================
- LangChain v0.3 包体大 + 抽象层叠多 (Tool → BaseTool → ChatPromptTemplate →
  AgentExecutor), 我们这里只要"name + description + parameters + async run", 60 行
  自己写更可控
- spec/06 走"精简包路线": LLM facade 已自维护, ReAct 主循环也会走 LangGraph
  纯节点级别, 中间不夹 LangChain Tool 抽象
- ``ToolResult`` 字段定死 ``ok / data / error / elapsed_ms``: 让 BE-S2-007 写
  ``chat_tool_calls`` 表时直接 ``json.dumps(result.data)`` / ``result.error``
  入库, 不需要再适配第三方 dataclass

Tool 沙盒
=========
注册的 ``async run`` 必须**不抛**: 入参校验失败 / 超时 / 上游异常一律走
``ToolResult(ok=False, error=...)``. 实现侧通过 ``sandbox.sandboxed`` 装饰器
统一兜底, 见 ``app/services/agent/sandbox.py``.

不在本 PR 做 (BE-S2-006b / 007)
================================
- LangGraph 主循环 / ReAct 步进 (BE-S2-007)
- Tool 调用回写 ``chat_tool_calls`` (BE-S2-007 在主循环内做, 这里只管"算结果")
- Tool 限频 / 单用户日预算 / 单 session step cap (BE-S2-007 主循环 + BE-S2-008 配额)
- ``hybrid_search`` Tool 包装 (BE-S2-006b 一并和 peers / sentiment / historical 落地)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.core.logging import logger


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Tool 调用结果. 全字段都可 JSON 序列化, 让 BE-S2-007 直接入 ``chat_tool_calls``.

    - ``ok``: True 时 ``data`` 非空, ``error`` 是 None; False 反之
    - ``data``: Tool 业务负载, 必须是 dict (LLM tool message content 走 JSON.stringify)
    - ``error``: 失败原因. **不带堆栈** (堆栈进 logger), 仅给 LLM 决定要不要换 Tool
    - ``elapsed_ms``: Tool 真实执行耗时 (含入参校验 + 沙盒开销), 给监控 / cost
      调试用
    """

    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    elapsed_ms: int = 0

    @classmethod
    def success(cls, data: dict[str, Any], elapsed_ms: int = 0) -> ToolResult:
        return cls(ok=True, data=data, elapsed_ms=elapsed_ms)

    @classmethod
    def failure(cls, error: str, elapsed_ms: int = 0) -> ToolResult:
        return cls(ok=False, error=error, elapsed_ms=elapsed_ms)


# Tool runner 协议: 接收已校验的 pydantic input model, 返回 ToolResult
# (具体类型由各 Tool 实现自定 input model; runner signature 用 Any 让 mypy 兼容)
ToolRunner = Callable[..., Awaitable[ToolResult]]


@dataclass(frozen=True, slots=True)
class Tool:
    """Tool 元信息 + runner. 不可变 (frozen) 防注册后被偷改.

    Fields
    ------
    - ``name``: 全局唯一, 必须能被 LLM tool_call 引用. 对齐 OpenAI 限制
      (``^[a-zA-Z0-9_-]{1,64}$``)
    - ``description``: 给 LLM 看的 1-3 句话简介; LLM 据此决定何时调
    - ``input_model``: pydantic ``BaseModel`` 子类, 描述入参. 注册时自动 dump 成
      JSON schema 灌进 OpenAI tool definition; runner 调用前用它 ``model_validate``
      再校
    - ``runner``: ``async (input: input_model_instance) -> ToolResult``;
      实现侧应当被 ``@sandboxed`` 装饰 (超时 + 异常归一)
    - ``timeout_seconds``: 沙盒超时 (秒); 仅 metadata, 实际生效在 ``sandboxed``
      装饰器侧, 这里冗余存一份给 BE-S2-007 主循环 step 调度时复用
    """

    name: str
    description: str
    input_model: type[BaseModel]
    runner: ToolRunner
    timeout_seconds: float = 10.0
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_openai_schema(self) -> dict[str, Any]:
        """转 OpenAI ``tools=[{type: function, function: {...}}]`` 单条 schema.

        注意 pydantic ``model_json_schema()`` 会带 ``$defs`` / ``title`` 等字段,
        OpenAI 接受但部分自托管 LLM (Qwen-2.5) 解析有问题; 本方法只移除 ``title``,
        保留 ``$defs`` (复合类型必需). 真有兼容性问题再做 schema 简化层.
        """
        params = self.input_model.model_json_schema()
        params.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }


# 模块级单例. 不暴露给外部直接 import; 走 ``register`` / ``get`` / ``list_all``
_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    """注册一个 Tool. 重名时 logger.warning + 替换 (热重载友好).

    raises: ``ValueError`` (tool.name 不合法 / runner 不是 awaitable callable)
    """
    if not tool.name or not _is_valid_tool_name(tool.name):
        raise ValueError(
            f"tool name {tool.name!r} 不合法; 仅允许 [a-zA-Z0-9_-], 1-64 字符"
        )
    if not callable(tool.runner):
        raise ValueError(f"tool {tool.name} runner 必须是 callable")
    if not issubclass(tool.input_model, BaseModel):
        raise ValueError(
            f"tool {tool.name} input_model 必须是 pydantic BaseModel 子类"
        )

    if tool.name in _REGISTRY:
        logger.warning(
            f"tool_registry.replace name={tool.name} "
            f"(old.runner={_REGISTRY[tool.name].runner.__qualname__}, "
            f"new.runner={tool.runner.__qualname__})"
        )
    _REGISTRY[tool.name] = tool
    logger.info(
        f"tool_registry.register name={tool.name} "
        f"timeout={tool.timeout_seconds}s tags={tool.tags}"
    )


def unregister(name: str) -> bool:
    """显式注销; 测试隔离用. 返回是否真删了."""
    return _REGISTRY.pop(name, None) is not None


def get(name: str) -> Tool | None:
    """按 name 取 Tool; 不存在返回 None (调用方需明确 None case)."""
    return _REGISTRY.get(name)


def list_all() -> list[Tool]:
    """所有已注册 Tool, 按 name 字典序返回 (确定性, 方便 snapshot 测).

    BE-S2-007 主循环若需"按 tag 过滤"可在这里二次过滤; 当前没必要.
    """
    return sorted(_REGISTRY.values(), key=lambda t: t.name)


def list_openai_schemas() -> list[dict[str, Any]]:
    """打包成 OpenAI ``tools=[...]`` 入参格式. 直接给 LLM facade 用."""
    return [t.to_openai_schema() for t in list_all()]


def clear_registry_for_test() -> None:
    """测试隔离专用: 清空 ``_REGISTRY``. **不要在生产代码调用**.

    pytest fixture 可在 setup/teardown 调一次, 不让别条测试的注册副作用串扰.
    """
    _REGISTRY.clear()


def _is_valid_tool_name(name: str) -> bool:
    """OpenAI tool name 约束: ``^[a-zA-Z0-9_-]{1,64}$``."""
    if not 1 <= len(name) <= 64:
        return False
    return all(c.isalnum() or c in "_-" for c in name)


__all__ = [
    "Tool",
    "ToolResult",
    "ToolRunner",
    "clear_registry_for_test",
    "get",
    "list_all",
    "list_openai_schemas",
    "register",
    "unregister",
]
