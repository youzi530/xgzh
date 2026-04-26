"""当前用户路由 (BE-003).

Sprint 1: 仅 ``GET /me``。
后续:
- BE-010 起 ``GET /me/favorites`` 移到 favorites.py 单独域
- 资料编辑 ``PATCH /me`` 进 FE-003 时再加 (头像上传依赖 OSS 走 BE-013, 还没排)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.db.models import User
from app.schemas.auth import UserPublic
from app.security import get_current_user

router = APIRouter(prefix="/me", tags=["me"])


@router.get(
    "",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
    summary="当前用户基本信息",
    responses={401: {"description": "未登录 / token 无效 / token 过期"}},
)
async def read_me(current_user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.model_validate(current_user)
