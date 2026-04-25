"""AI Agent 业务服务 (第一刀: 单轮诊断, 不含 RAG / Tool Use).

后续 Sprint 2 按 spec/04 §3 演进为 LangGraph + Tool Use + RAG。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.adapters import llm_client
from app.schemas.agent import DiagnoseRequest
from app.schemas.ipo import IPOItem

# ─── System Prompt（中立护栏，不可删减，对应 .cursor/rules/30/50） ─────
SYSTEM_PROMPT = """你是新股智汇 (XGZH) 的金融分析助手。必须严格遵守以下规则：

【数据真实性】
1. 所有数字、事实必须来源于用户提供的上下文，禁止凭记忆编造
2. 如缺少数据，必须明确说"暂无足够数据"
3. 引用使用 [1][2] 格式

【中立性 - 红线】
1. 严禁使用："建议买入/满仓/重仓/全仓/必涨/稳赚/抄底/保本/保收益/all in/梭哈"
2. 仅做事实陈述与多方观点呈现，给出"机会与风险"两面分析
3. 必须以"以上为客观分析，最终决策请结合自身情况，本工具不构成投资建议"结尾

【输出格式】
请使用 Markdown，按以下 5 个维度组织（每段 50-120 字）：
1. **基本面摘要**（PE/募资/行业地位）
2. **核心风险点 Top 3**
3. **多空观点**
4. **上市预期**（区间, 不给具体数字承诺）
5. **同业对比**（如有可比公司）

【安全】
- 拒绝回答与新股 / 金融分析无关的问题，礼貌引导回主题
"""


def _build_user_prompt(ipo: IPOItem | None, req: DiagnoseRequest) -> str:
    """把新股结构化数据变成给模型的上下文。"""
    parts: list[str] = []
    parts.append(f"# 待分析新股\n\n- 代码: {req.code}")
    if req.name:
        parts.append(f"- 名称: {req.name}")

    if ipo is not None:
        parts.append(f"- 市场: {ipo.market}")
        if ipo.industry:
            parts.append(f"- 行业: {ipo.industry}")
        if ipo.issue_price is not None:
            parts.append(f"- 发行价: {ipo.issue_price} {ipo.issue_currency or ''}")
        if ipo.pe_ratio is not None:
            parts.append(f"- 发行市盈率 (PE): {ipo.pe_ratio}")
        if ipo.raised_amount is not None:
            parts.append(f"- 募资金额: {ipo.raised_amount}")
        if ipo.listing_date is not None:
            parts.append(f"- 上市日期: {ipo.listing_date.isoformat()}")
        if ipo.one_lot_winning_rate is not None:
            parts.append(f"- 一手中签率: {ipo.one_lot_winning_rate}")
    else:
        parts.append('- 注意: 暂未在数据源命中该代码, 请以"暂无足够数据"为前提作答')

    parts.append("")
    if req.question:
        parts.append(f"# 用户问题\n\n{req.question}")
    else:
        parts.append("# 用户问题\n\n请按系统约定的 5 个维度做一份基础诊断。")

    return "\n".join(parts)


async def diagnose_stream(
    req: DiagnoseRequest,
    ipo: IPOItem | None,
) -> AsyncIterator[str]:
    """流式诊断, 直接 yield token。"""
    user_prompt = _build_user_prompt(ipo, req)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    async for delta in llm_client.stream_chat(messages):
        yield delta
