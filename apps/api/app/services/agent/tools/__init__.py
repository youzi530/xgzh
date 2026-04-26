"""Tool 实现集 (BE-S2-006a / 006b).

模块入口
========
import 本包时, 通过 ``_AUTO_REGISTER`` 列表挨个 import 子模块, 子模块 import
时会自动调 ``tool_registry.register()`` 把 Tool 注入全局 ``_REGISTRY``. 这样
``app.services.agent`` 在最外层 ``from app.services.agent import tool_registry,
sandbox`` 之后就能 ``list_all()`` 拿全部 Tool, 不需要主循环手动 import 每个文件.

为什么走"模块 side effect 注册" 而非"显式 register list"
========================================================
- BE-S2-006b / BE-S2-007 后会有 5+ Tool, 写显式 list 容易漏注册 (尤其新人补 Tool
  时); side effect import 让"加 Tool" = "加文件 + 加 import 一行"
- 单测可以 ``clear_registry_for_test()`` 后再 ``import_module`` 重置, 不需要破
  坏全局状态

Tool 清单 (与 spec/04 §3.1 对齐)
================================
- ``get_ipo_basic_info``: IPO 基础信息 (BE-S2-006a, 本 PR)
- ``get_financial_statements``: 财务报表 / 财务摘要 (BE-S2-006a, 本 PR)
- ``get_peer_comparison``: 同业对标 (BE-S2-006b)
- ``get_sentiment_summary``: 情感分布 (BE-S2-006b, placeholder, 占位实现)
- ``get_historical_winning_rate``: 历史中签率 (BE-S2-006b)
- ``hybrid_search``: 招股书 RAG 检索 (BE-S2-006b 包装 services/rag/hybrid_search.py)

side effect: import 各子模块 → 触发 ``register(...)``.
"""

from __future__ import annotations

# 显式 import 子模块, 触发模块级 register() side effect
from app.services.agent.tools import basic_info, financial

__all__ = ["basic_info", "financial"]
