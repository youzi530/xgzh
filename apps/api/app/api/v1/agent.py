"""AI Agent 路由: 流式 SSE 输出 (spec/04)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.schemas.agent import DiagnoseRequest
from app.services import agent_service, ipo_service

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
