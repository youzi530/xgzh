"""AI Agent 路由: 流式 SSE 输出 (spec/04)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.cache import rate_limit
from app.db.models import User
from app.schemas.agent import DiagnoseRequest, HistoricalPatternRequest
from app.security.deps import get_current_user
from app.services import agent_service, ipo_service
from app.services.agent.historical_pattern import historical_pattern_stream

router = APIRouter(prefix="/agent", tags=["agent"])


def _sse_event(event_type: str, data: dict[str, Any] | str) -> dict[str, str]:
    payload = data if isinstance(data, dict) else {"content": data}
    return {"event": event_type, "data": json.dumps(payload, ensure_ascii=False)}


@router.post("/diagnose")
async def diagnose(req: DiagnoseRequest) -> EventSourceResponse:
    """新股一键诊断 SSE 流."""
    ipo = await ipo_service.get_ipo(req.code)

    async def generator() -> AsyncIterator[dict[str, str]]:
        yield _sse_event(
            "start",
            {
                "code": req.code,
                "name": (ipo.name if ipo else req.name) or "",
                "found_in_source": ipo is not None,
            },
        )

        try:
            async for delta in agent_service.diagnose_stream(req, ipo):
                if not delta:
                    continue
                yield _sse_event("delta", {"content": delta})
        except Exception as e:
            yield _sse_event(
                "error", {"message": f"{type(e).__name__}: {e}"}
            )
            return

        yield _sse_event("end", {"ok": True})

    return EventSourceResponse(generator(), ping=15)


# ─── BE-S4-004 AI 历史规律分析报告 ──────────────────────────────────


def _hp_rate_limit_key(
    req: HistoricalPatternRequest,  # noqa: ARG001 - 形参名对齐 rate_limit kwargs
    user: User,
    request: Request,  # noqa: ARG001
) -> str:
    """rate-limit key: 单用户 5 次/min (DeepSeek-R1 慢且贵, 严控)."""
    return f"user:{user.user_id}"


@router.post("/historical-pattern")
@rate_limit(
    times=5,
    per_seconds=60,
    namespace="agent_hp",
    key_func=_hp_rate_limit_key,
)
async def historical_pattern(
    req: HistoricalPatternRequest,
    user: Annotated[User, Depends(get_current_user)],
    request: Request,  # noqa: ARG001 — 形参 rate_limit 占位用
) -> EventSourceResponse:
    """AI 历史规律分析报告 SSE (BE-S4-004).

    - **认证**: 必须登录 (Bearer token); 严控成本
    - **限流**: 单用户 5 次/min (DeepSeek-R1 调用慢且贵)
    - **缓存**: (industry / market / year_from / year_to) 30 min;
      命中后无 LLM 调用, 直接重放
    - **fallback**: DeepSeek-R1 不可用 → GLM-4-Flash; 双失败返 ``event: error``
    - **合规**: ``forbidden_pattern_filter`` 写缓存前应用 + ``ensure_disclaimer`` 兜底

    SSE 协议: 见 ``app.services.agent.historical_pattern.historical_pattern_stream`` 文档.
    """
    user_id_for_log = str(user.user_id)

    async def generator() -> AsyncIterator[dict[str, str]]:
        try:
            async for ev in historical_pattern_stream(req):
                yield _sse_event(ev["event"], ev["data"])
        except Exception as e:  # noqa: BLE001
            yield _sse_event(
                "error",
                {
                    "code": "internal_error",
                    "message": f"{type(e).__name__}: {e}",
                },
            )

    # rate_limit 装饰器在路由层已挡 (超限 raise 转 429); 不会进 generator
    _ = user_id_for_log
    return EventSourceResponse(generator(), ping=15)
