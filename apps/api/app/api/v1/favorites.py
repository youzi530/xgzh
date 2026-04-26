"""自选股路由 (BE-010, spec/03 §1.5).

- ``POST   /api/v1/favorites``         登录态; 幂等添加, 返回 200 + ``created`` 标识
- ``DELETE /api/v1/favorites/{code}``  登录态; 幂等删除, 返回 200 + ``removed`` 标识
- ``GET    /api/v1/favorites``         登录态; 用户全部自选 + 最新行情 (LEFT JOIN ipos)

错误码:
- 400 ``favorite_code_invalid``: code 没带后缀 / 后缀不在白名单
- 401 ``token_*`` (六种): 复用 BE-003 鉴权 deps 的全部 401 reason
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.schemas.favorite import (
    FavoriteAddRequest,
    FavoriteAddResponse,
    FavoriteListResponse,
    FavoriteRemoveResponse,
)
from app.security import get_current_user
from app.services import favorite_service
from app.services.favorite_service import FavoriteCodeInvalidError

router = APIRouter(prefix="/favorites", tags=["favorites"])


def _bad_code(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "favorite_code_invalid", "message": message},
    )


@router.post(
    "",
    response_model=FavoriteAddResponse,
    status_code=status.HTTP_200_OK,
    summary="添加自选 (幂等)",
    responses={
        400: {"description": "code 不合法 (无后缀 / 未知后缀)"},
        401: {"description": "未登录"},
    },
)
async def add_favorite(
    req: FavoriteAddRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FavoriteAddResponse:
    try:
        result = await favorite_service.add_favorite(
            session,
            user_id=current_user.user_id,
            code=req.code,
            notify_on_subscribe=req.notify_on_subscribe,
        )
    except FavoriteCodeInvalidError as e:
        raise _bad_code(str(e)) from e

    return FavoriteAddResponse(
        ok=True,
        code=result.code,
        market=result.market,
        notify_on_subscribe=result.notify_on_subscribe,
        favorited_at=result.favorited_at,
        created=result.created,
    )


@router.delete(
    "/{code}",
    response_model=FavoriteRemoveResponse,
    status_code=status.HTTP_200_OK,
    summary="移除自选 (幂等; 不存在也返回 200)",
    responses={
        400: {"description": "code 不合法"},
        401: {"description": "未登录"},
    },
)
async def remove_favorite(
    code: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FavoriteRemoveResponse:
    try:
        code_norm, market, removed = await favorite_service.remove_favorite(
            session,
            user_id=current_user.user_id,
            code=code,
        )
    except FavoriteCodeInvalidError as e:
        raise _bad_code(str(e)) from e

    return FavoriteRemoveResponse(
        ok=True,
        code=code_norm,
        market=market,
        removed=removed,
    )


@router.get(
    "",
    response_model=FavoriteListResponse,
    summary="当前用户的全部自选 (LEFT JOIN ipos 拿最新行情)",
    responses={401: {"description": "未登录"}},
)
async def list_favorites(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FavoriteListResponse:
    return await favorite_service.list_favorites(
        session, user_id=current_user.user_id
    )
