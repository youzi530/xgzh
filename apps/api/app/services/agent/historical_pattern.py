"""BE-S4-004 AI 历史规律分析报告 SSE 服务.

调用链: 候选历史 IPO 池 (BE-S4-003 ``list_historical_ipos``) → DeepSeek-R1 思维链
推理 (fallback GLM-4-Flash via ``llm_client.chat`` model 参数指定; 真上游路由由
``app.adapters.llm_client._build_completion_kwargs`` 决定) → 5 段结构化报告 +
引用源 → 端层 ``forbidden_pattern_filter`` 兜底 + ``ensure_disclaimer``.

设计要点
========
1. **非流式 LLM + 端层切片重放**: ``llm_client.chat`` (非流) 拿全 text → ``@cached``
   30 min 缓存 → SSE generator 按 ~30 字符切片重放给前端. 这样比 stream_chat
   写缓存的复杂度低 (流不能直接 cache + 边流边过滤违禁词); 用户感知"流式"靠
   重放节奏 (~30ms/chunk = 接近真流), 实际后台一次性返回 + 缓存命中后 0 LLM
   调用. spec/04 §3 进阶分析允许 LLM 单次 10-30s, 重放体验比"卡 30s 后一次性
   出 2000 字"好 10 倍.
2. **`@cached(namespace='agent:hp')` 自动幂等**: 同 (industry / market / year
   范围) 30 min 内只调一次 LLM; 缓存的是清洗后 (forbidden_pattern_filter 过)
   的 text, 重放时永远不会泄违禁词
3. **候选池 < 5 时不调 LLM**: 直接 emit ``event: error code=insufficient_data``,
   省成本; 与 BE-S4-003 ``compute_peer_aggregate`` insufficient_data 兜底对齐
4. **forbidden_pattern_filter 在写缓存前应用**: 一过则不再过, 缓存里永远是干净
   text; 端层再 ensure_disclaimer 兜免责声明 (即便缓存里历史版本没声明也兜)

SSE 协议 (与 chat_diagnose 同款 sse-starlette)
=============================================
- ``event: start`` ``data: {industry, peer_count, sample_size, market}``
- ``event: delta`` ``data: {content: "<chunk>"}`` (重复 N 次)
- ``event: citations`` ``data: {sources: [{code, name, listing_date, ...}]}``
- ``event: end`` ``data: {ok: true, warnings: [...]}``
- ``event: error`` ``data: {code: "insufficient_data" | "llm_error", message: "..."}``
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from app.adapters import llm_client
from app.cache import cached
from app.core.logging import logger
from app.schemas.agent import HistoricalPatternRequest
from app.services import ipo_service

# 候选池采样: top N 历史 IPO 当作 LLM 上下文 (按 listing_date DESC)
PATTERN_SAMPLE_SIZE = 50
# 最少样本: < 5 直接返 insufficient_data 不调 LLM
PATTERN_MIN_SAMPLES = 5
# SSE delta 重放切片 (字符级; 30 字符 ≈ 6-10 中文字 1 chunk)
SSE_REPLAY_CHUNK_SIZE = 30
# SSE delta 重放节奏 (ms gap): 30ms ≈ 接近真流 token 速度
SSE_REPLAY_GAP_SECONDS = 0.03
# LLM cache TTL (默认 30 min, 业务侧可旁路)
PATTERN_CACHE_TTL_SECONDS = 1800
# 默认 LLM 模型 (DeepSeek-R1 思维链最强; 实际路由由 _build_completion_kwargs 决定)
PATTERN_PRIMARY_MODEL = "deepseek-reasoner"
PATTERN_FALLBACK_MODEL = "glm-4-flash"


# ─── system prompt: 锁 markdown 5 段结构 + 中立护栏 ────────────────


SYSTEM_PROMPT = """你是新股智汇 (XGZH) 的资深金融分析师, 擅长用历史 IPO 数据找规律.
必须严格遵守以下规则:

【数据真实性】
1. 所有数字 / 事实必须来源于用户提供的"候选历史 IPO 池", 禁止凭记忆编造
2. 没足够样本时明确说"暂无足够数据"
3. 引用源使用 [代码-序号] 格式, 例如 "腾讯 [00700.HK]" / "美团 [03690.HK]"

【中立性 - 红线】
1. 严禁使用: "建议买入 / 满仓 / 重仓 / 全仓 / 必涨 / 稳赚 / 抄底 / 保本 / 保收益 / all in / 梭哈 / 包赚 / 打新必中"
2. 仅做事实陈述 + 多方观点, 给"机会与风险"两面分析
3. 必须以"以上为客观分析, 最终决策请结合自身情况, 本工具不构成投资建议"结尾

【输出格式 - 必须 5 段, Markdown 标题 + emoji 锚点】
请按以下 5 段输出, 每段 80-150 字:

### 📊 行业首日涨幅分布
mean / median / p25 / p75 / min / max 数据点 + 直观解读 (这个行业的"打新热度"如何)

### 📈 估值 vs 涨幅相关性
PE 高低与首日涨幅的关系 (正相关 / 负相关 / 弱相关) + 强度评估; 列 2-3 个对比案例

### 🏆 顶部分位 (前 25% 涨幅) 共性
列出该分位 IPO 的共性: 行业子领域 / 估值区间 / 募资规模 / 保荐人; 给 3 个具体例子

### ⚠️ 底部分位 (后 25% 涨幅) 共性 + 风险信号
列出底部 IPO 的共性 / 风险信号; 给 3 个具体例子, 提醒散户哪些信号要警惕

### 💡 Top 3 启示 + 当前 IPO 参考
基于上述分析给"当前用户该如何看待此行业 IPO"的 Top 3 启示;
若用户提供 ``current_ipo_code``, 指出它在分布中的分位 (前 25% / 中段 / 后 25%) 并比较

【安全】
- 拒绝回答与新股 / 金融分析无关的问题, 礼貌引导回主题
- 不能给出"这只股票将上涨 X%"的具体数字承诺
"""


# ─── 候选池采样 ─────────────────────────────────────────────────────


async def _sample_candidates(
    req: HistoricalPatternRequest,
) -> list[dict[str, Any]]:
    """走 BE-S4-003 ``list_historical_ipos`` 拉候选池 (按 listing_date DESC).

    取 top ``PATTERN_SAMPLE_SIZE`` 行;< ``PATTERN_MIN_SAMPLES`` 时上层直接错出.
    """
    payload = await ipo_service.list_historical_ipos(
        market=req.market,
        industry=req.industry,
        year_from=req.year_from,
        year_to=req.year_to,
        sort_by="listing_date",
        page=1,
        size=PATTERN_SAMPLE_SIZE,
    )
    items = payload.get("items", []) if isinstance(payload, dict) else []
    return list(items)


def _build_user_prompt(
    req: HistoricalPatternRequest, candidates: list[dict[str, Any]]
) -> str:
    """把候选池序列化成 LLM context."""
    parts: list[str] = []
    parts.append(
        f"# 行业: {req.industry}\n# 市场: {req.market or '全市场 (HK + A)'}\n"
        f"# 时间窗: {req.year_from} ~ {req.year_to}\n"
        f"# 候选池: 共 {len(candidates)} 只历史 IPO (按上市时间倒排)\n"
    )
    parts.append("## 候选历史 IPO 列表\n")
    for it in candidates:
        line = (
            f"- [{it.get('code')}] {it.get('name')} | "
            f"上市: {it.get('listing_date')} | "
            f"行业: {it.get('industry')} ({it.get('industry_l2') or ''}) | "
            f"PE: {it.get('pe_ratio') or 'N/A'} | "
            f"募资: {it.get('raised_amount') or 'N/A'} | "
            f"首日涨跌: {it.get('first_day_change_pct')}%"
        )
        if it.get("oversubscribe_multiple") is not None:
            line += f" | 认购倍数: {it.get('oversubscribe_multiple')}x"
        if it.get("one_lot_winning_rate") is not None:
            line += f" | 中签率: {it.get('one_lot_winning_rate')}"
        sponsors = it.get("sponsors")
        if sponsors:
            line += f" | 保荐: {', '.join(sponsors[:2])}"
        parts.append(line)

    parts.append("")
    if req.current_ipo_code:
        parts.append(
            f"## 用户上下文\n当前 IPO: {req.current_ipo_code}; 请在 §💡 段指出它在分布中的位置."
        )
    parts.append(
        "\n请严格按 system prompt 5 段格式 + Markdown emoji 锚点输出. 字数控制 80-150 字 / 段."
    )
    return "\n".join(parts)


# ─── LLM 调用 (non-stream, cached) ───────────────────────────────────


@cached(
    ttl_seconds=PATTERN_CACHE_TTL_SECONDS,
    namespace="agent:hp",
    skip_if_none=True,
)
async def _generate_pattern_text(
    *,
    industry: str,
    market: str | None,
    year_from: int,
    year_to: int,
    current_ipo_code: str | None,
    candidates_summary: str,
) -> dict[str, Any] | None:
    """LLM 一次性生成 5 段历史规律分析报告.

    返回 ``{"content": str, "warnings": list[str], "model": str}``; ``@cached``
    用 ``json.dumps`` 序列化, 命中后直接 dict 返回.

    DeepSeek-R1 → fallback GLM-4-Flash:
    - 优先 ``deepseek-reasoner`` (思维链); 失败 / 超时 fallback ``glm-4-flash``
    - fallback 时 logger.warning + 端层 warnings 字段记录, FE 可显示"⚠️ 思维链
      引擎不可用, 走快速版本"
    - 双失败 → 返 None, 上层 SSE 转 ``event: error code=llm_error``
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": candidates_summary},
    ]
    warnings: list[str] = []
    used_model = PATTERN_PRIMARY_MODEL

    try:
        result = await llm_client.chat(
            messages,
            model=PATTERN_PRIMARY_MODEL,
            temperature=0.4,
            max_tokens=2000,
        )
        content = result.content
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"agent.hp.primary_failed model={PATTERN_PRIMARY_MODEL} "
            f"err={type(e).__name__}: {e} → fallback {PATTERN_FALLBACK_MODEL}"
        )
        warnings.append(
            f"primary_model_unavailable: {PATTERN_PRIMARY_MODEL}; fallback={PATTERN_FALLBACK_MODEL}"
        )
        used_model = PATTERN_FALLBACK_MODEL
        try:
            result = await llm_client.chat(
                messages,
                model=PATTERN_FALLBACK_MODEL,
                temperature=0.4,
                max_tokens=2000,
            )
            content = result.content
        except Exception as e2:  # noqa: BLE001
            logger.error(
                f"agent.hp.fallback_failed model={PATTERN_FALLBACK_MODEL} "
                f"err={type(e2).__name__}: {e2}"
            )
            return None

    cleaned, hits = llm_client.forbidden_pattern_filter(content)
    if hits:
        logger.warning(
            f"agent.hp.forbidden_patterns_filtered count={len(hits)} hits={hits}"
        )
        warnings.append(f"forbidden_patterns_filtered: {hits}")

    final_text = llm_client.ensure_disclaimer(cleaned)

    # 一些不打 LLM key 的环境会拿到很短或空内容 — log 一下方便排查
    if not final_text.strip():
        logger.warning(
            f"agent.hp.empty_content industry={industry} model={used_model}"
        )
        return None

    # 上下文未直接用但参与缓存 key 区分: 不用就被 ruff 标
    _ = (
        industry,
        market,
        year_from,
        year_to,
        current_ipo_code,
    )
    return {
        "content": final_text,
        "warnings": warnings,
        "model": used_model,
    }


# ─── SSE 流主入口 ───────────────────────────────────────────────────


def _candidate_to_citation(it: dict[str, Any]) -> dict[str, Any]:
    """候选池行 → 引用源 dot (slim 化, 给 FE 跳详情用)."""
    return {
        "code": it.get("code"),
        "name": it.get("name"),
        "listing_date": it.get("listing_date"),
        "first_day_change_pct": it.get("first_day_change_pct"),
        "industry_l2": it.get("industry_l2"),
        "market": it.get("market"),
    }


async def historical_pattern_stream(
    req: HistoricalPatternRequest,
) -> AsyncIterator[dict[str, Any]]:
    """主 SSE 生成器 (BE-S4-004 ``POST /agent/historical-pattern``).

    协议 (与 chat_diagnose 同款 sse-starlette dict 形式):

    - ``{event: "start", data: {industry, market, peer_count, sample_size, year_from, year_to}}``
    - ``{event: "delta", data: {content: "<chunk>"}}`` 重复 N 次
    - ``{event: "citations", data: {sources: [{code, name, listing_date, first_day_change_pct, ...}]}}``
    - ``{event: "end", data: {ok: true, model: "...", warnings: [...]}}``
    - ``{event: "error", data: {code: "insufficient_data" | "llm_error", message: "..."}}``
    """
    candidates = await _sample_candidates(req)

    if len(candidates) < PATTERN_MIN_SAMPLES:
        yield {
            "event": "error",
            "data": {
                "code": "insufficient_data",
                "message": (
                    f"候选池仅 {len(candidates)} 条 < {PATTERN_MIN_SAMPLES}; "
                    f"行业 / 时间窗 / 市场组合下历史样本不足, 暂无法生成规律分析."
                ),
                "peer_count": len(candidates),
            },
        }
        return

    yield {
        "event": "start",
        "data": {
            "industry": req.industry,
            "market": req.market,
            "year_from": req.year_from,
            "year_to": req.year_to,
            "peer_count": len(candidates),
            "sample_size": PATTERN_SAMPLE_SIZE,
            "current_ipo_code": req.current_ipo_code,
        },
    }

    user_prompt = _build_user_prompt(req, candidates)

    payload = await _generate_pattern_text(
        industry=req.industry,
        market=req.market,
        year_from=req.year_from,
        year_to=req.year_to,
        current_ipo_code=req.current_ipo_code,
        candidates_summary=user_prompt,
    )

    if payload is None:
        yield {
            "event": "error",
            "data": {
                "code": "llm_error",
                "message": "DeepSeek-R1 + GLM-4-Flash 双双不可用; 请稍后重试.",
            },
        }
        return

    full_text = payload.get("content", "")
    warnings = payload.get("warnings", [])
    used_model = payload.get("model", "")

    # 切片重放 (SSE_REPLAY_CHUNK_SIZE 字符 / 帧, SSE_REPLAY_GAP_SECONDS gap)
    for i in range(0, len(full_text), SSE_REPLAY_CHUNK_SIZE):
        chunk = full_text[i : i + SSE_REPLAY_CHUNK_SIZE]
        yield {"event": "delta", "data": {"content": chunk}}
        if SSE_REPLAY_GAP_SECONDS > 0:
            await asyncio.sleep(SSE_REPLAY_GAP_SECONDS)

    yield {
        "event": "citations",
        "data": {
            "sources": [_candidate_to_citation(c) for c in candidates[:8]],
            "total": len(candidates),
        },
    }

    yield {
        "event": "end",
        "data": {
            "ok": True,
            "model": used_model,
            "warnings": warnings,
        },
    }


__all__ = [
    "HistoricalPatternRequest",
    "historical_pattern_stream",
    "PATTERN_SAMPLE_SIZE",
    "PATTERN_MIN_SAMPLES",
    "PATTERN_CACHE_TTL_SECONDS",
    "PATTERN_PRIMARY_MODEL",
    "PATTERN_FALLBACK_MODEL",
    "SYSTEM_PROMPT",
]
