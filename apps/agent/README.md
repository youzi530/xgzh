# xgzh-agent (placeholder)

> 当前状态：**占位**。第一刀阶段 Agent 逻辑直接放在 `apps/api/app/services/agent_service.py`。

## 拆分时机

按 `spec/07` Sprint 2 计划，当出现以下条件之一时拆分独立服务：

- AI 调用并发 > 主 API 5 倍
- 引入 LangGraph + 多步 Tool Use（独立部署可单独水平扩展）
- 需要独立的 GPU/CPU 资源（如本地 reranker 模型）

## 拆分时迁移路径

1. `apps/api/app/services/agent_*.py` → `apps/agent/app/services/`
2. `apps/api/app/adapters/llm_client.py` → `apps/agent/app/adapters/`
3. 主 API 通过 HTTP 内网调用 agent
