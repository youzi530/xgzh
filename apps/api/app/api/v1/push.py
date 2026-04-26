"""推送 token 路由 (BE-011, spec/03 §6 推送通知).

- ``POST   /api/v1/push/tokens``                            登录态; 幂等注册 / 覆盖
- ``DELETE /api/v1/push/tokens?platform=&device_id=``       登录态; 幂等注销

本 Sprint 不实施推送 (排到 Sprint 4); 这里只做"客户端把推送 token 注册到后端".
设计 contract 让前端 FE-001 / FE-002 在登录后即可立刻调, 把后端"推送候选名单"
养起来; Sprint 4 接入 APNs / FCM 时用 :func:`push_service.list_user_tokens` 取数即可.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.schemas.push import (
    PushPlatform,
    PushTokenRegisterRequest,
    PushTokenRegisterResponse,
    PushTokenUnregisterResponse,
)
from app.security import get_current_user
from app.services import push_service

router = APIRouter(prefix="/push", tags=["push"])


@router.post(
    "/tokens",
    response_model=PushTokenRegisterResponse,
    status_code=status.HTTP_200_OK,
    summary="注册推送 token (幂等; 同 device 复发会刷新 token)",
    responses={
        401: {"description": "未登录"},
        422: {"description": "platform 不在白名单 / token 太短 / device_id 为空"},
    },
)
async def register_token(
    req: PushTokenRegisterRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PushTokenRegisterResponse:
    result = await push_service.register_token(
        session,
        user_id=current_user.user_id,
        platform=req.platform,
        token=req.token,
        device_id=req.device_id,
    )
    return PushTokenRegisterResponse(
        ok=True,
        id=result.id,
        platform=result.platform,
        device_id=result.device_id,
        is_active=result.is_active,
        created=result.created,
        registered_at=result.registered_at,
    )


@router.delete(
    "/tokens",
    response_model=PushTokenUnregisterResponse,
    status_code=status.HTTP_200_OK,
    summary="注销推送 token (幂等; 不存在也返 200)",
    responses={401: {"description": "未登录"}, 422: {"description": "参数缺失"}},
)
async def unregister_token(
    platform: Annotated[
        PushPlatform, Query(description="推送平台: ios / android / wxmp / h5")
    ],
    device_id: Annotated[
        str,
        Query(min_length=1, max_length=64, description="要注销的设备 id"),
    ],
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PushTokenUnregisterResponse:
    removed = await push_service.unregister_token(
        session,
        user_id=current_user.user_id,
        platform=platform,
        device_id=device_id,
    )
    return PushTokenUnregisterResponse(
        ok=True,
        platform=platform,
        device_id=device_id,
        removed=removed,
    )
