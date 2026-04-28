"""反馈路由 (Sprint 5 BE-S5-004).

公开端点 (匿名 + 登录都能调):
- ``POST /api/v1/feedback`` 提交一条反馈

admin 端列表查询走 ``app/api/v1/admin.py::list_feedbacks`` (X-Admin-Token 鉴权).

限流策略 (spec/12 §AC, 走 ``feedback_service.enforce_rate_limit``):
- 匿名 IP: 5 min ≤ 3 条
- 登录用户: 1h ≤ 10 条
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.schemas.feedback import (
    FeedbackCreateRequest,
    FeedbackCreateResponse,
)
from app.security import get_optional_user
from app.services import feedback_service

router = APIRouter(prefix="/feedback", tags=["feedback"])


def _resolve_client_ip(request: Request) -> str | None:
    """匿名限流 key 用的 IP. 优先 ``X-Forwarded-For`` 第一段 (反代场景),
    fallback 到 ``request.client.host`` (直连 / 本地测试).

    与 ``brokers.py`` / ``chat.py`` 同款语义 (项目暂无 utils 公共模块, 复刻避免循环).
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client is not None:
        return request.client.host
    return None


@router.post(
    "",
    response_model=FeedbackCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="提交一条反馈 (匿名 / 登录都可)",
    responses={
        201: {"description": "已收到, 客服会在 3 工作日内通过留下的联系方式回复"},
        422: {"description": "字段校验失败 (category 不在枚举 / content 超长)"},
        429: {"description": "提交过于频繁, 请稍后再试 (Retry-After header)"},
    },
)
async def create_feedback(
    req: FeedbackCreateRequest,
    request: Request,
    user: User | None = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
) -> FeedbackCreateResponse:
    """收到一条用户反馈; admin 在 ``GET /api/v1/admin/feedbacks`` 拉清单."""
    user_id = user.user_id if user is not None else None
    client_ip = _resolve_client_ip(request)

    # 限流: 超限直接 raise RateLimitExceeded → main.py 全局 handler 转 429
    await feedback_service.enforce_rate_limit(
        user_id=user_id, client_ip=client_ip
    )

    result = await feedback_service.create_feedback(
        session,
        user_id=user_id,
        category=req.category,
        content=req.content,
        contact=req.contact,
        app_version=req.app_version,
        platform=req.platform,
        client_ip=client_ip,
    )
    await session.commit()
    return FeedbackCreateResponse(
        feedback_id=result.feedback_id,
        created_at=result.created_at,
    )
