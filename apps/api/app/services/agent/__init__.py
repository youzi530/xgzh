"""Agent 子系统 (Sprint 2 BE-S2-006a / 006b / 007).

模块组织
========
- ``tool_registry``: Tool 抽象 + 注册中心 + OpenAI tools schema 自动生成 (BE-S2-006a)
- ``sandbox``: ``@sandboxed`` 装饰器, 超时 + 异常归一 + pydantic 入参校验 (BE-S2-006a)
- ``tools/``: 具体 Tool 实现 (BE-S2-006a 提供 basic_info / financial; BE-S2-006b
  追加 peers / sentiment / historical; BE-S2-005 hybrid_search 也通过这里包装)
- ``main_loop`` (BE-S2-007): LangGraph ReAct 主循环 + 引用源装配

为什么单独建 ``services/agent/`` 包
====================================
1. 与 ``services/rag/`` (检索原语) 解耦: RAG 只管"给文本召回 chunk", agent 包负责
   "把 5 个 Tool 注入给 LLM + 跑 ReAct + 装配引用源 + 写 chat_messages"
2. BE-S2-006a → 006b → 007 三个 PR 都会向这里追加文件, 提前留好命名空间
3. ``services/agent_service.py`` (Sprint 1 老 SSE diagnose) 保持不动, 作为
   Tool Use 失败时的 fallback 路径

入口
====
``from app.services.agent import tool_registry, sandbox`` 是默认入口;
具体 Tool 通过 ``tools/__init__.py`` 在 module import 时自动注册到全局
``_REGISTRY``, BE-S2-007 主循环只需 ``tool_registry.list_openai_schemas()``
就拿全部可用 Tool.
"""

from app.services.agent import sandbox, tool_registry
from app.services.agent import tools as _tools  # 触发自注册 side effect

__all__ = ["sandbox", "tool_registry"]


_ = _tools  # 防 linter 报 F401
