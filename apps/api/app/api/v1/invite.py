"""邀请码路由 (BE-006).

- ``POST /api/v1/invite/bind`` 在登录态下绑定 referrer (一次性)。

目前只暴露这一条; 邀请码本身查询 (``GET /me/invite``) 留给 FE-003 个人中心拉来用,
那时候直接读 ``GET /me`` 里的 ``invite_code`` 字段即可, 不必单开接口。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import rate_limit
from app.db import get_session
from app.db.models import User
from app.schemas.invite import InviteBindRequest, InviteBindResponse
from app.security import get_current_user
from app.services import invite_service
from app.services.invite_service import (
    InviteAlreadyBoundError,
    InviteCodeExhaustedError,
    InviteCodeExpiredError,
    InviteCodeInactiveError,
    InviteCodeNotFoundError,
    InviteCodeNotPersonalError,
    InviteSelfBindError,
)

router = APIRouter(prefix="/invite", tags=["invite"])


def _bind_rate_limit_key(
    req: InviteBindRequest, current_user: User, **_: object
) -> str:
    """限流 key 取 user_id, 防止用户拿一堆 code 暴力试 (即使一次性也别让试).

    ⚠️ 形参名必须与路由签名一致 (FastAPI 把 ``req`` / ``current_user`` 都按 kwargs
    传给 ``rate_limit`` 装饰器), 否则 missing positional. ``req`` 这里不直接用,
    但保留它否则 kwargs 里有同名 key 会撞。
    """
    _ = req  # 仅用于参数签名对齐
    return f"user:{current_user.user_id}"


@router.post(
    "/bind",
    response_model=InviteBindResponse,
    status_code=status.HTTP_200_OK,
    summary="绑定邀请人 (一次性, 不可改)",
    responses={
        400: {"description": "已绑过 / 自禁 / 码不可用"},
        401: {"description": "未登录"},
        404: {"description": "邀请码不存在"},
        429: {"description": "尝试次数过多"},
    },
)
@rate_limit(
    times=10,
    per_seconds=60,
    namespace="invite_bind",
    key_func=_bind_rate_limit_key,
)
async def bind_invite(
    req: InviteBindRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> InviteBindResponse:
    try:
        result = await invite_service.bind_invite(
            session, current_user=current_user, code=req.code
        )
    except InviteCodeNotFoundError as e:
        logger.info(f"invite.bind.not_found user_id={current_user.user_id} code={req.code}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "invite_code_not_found", "message": "邀请码不存在"},
        ) from e
    except InviteSelfBindError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invite_self_binding", "message": "不能绑定自己的邀请码"},
        ) from e
    except InviteAlreadyBoundError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invite_already_bound", "message": "已经绑定过邀请人, 不可更改"},
        ) from e
    except InviteCodeInactiveError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invite_code_inactive", "message": "邀请码已被禁用"},
        ) from e
    except InviteCodeExpiredError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invite_code_expired", "message": "邀请码已过期"},
        ) from e
    except InviteCodeExhaustedError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invite_code_exhausted",
                "message": "邀请码使用次数已满",
            },
        ) from e
    except InviteCodeNotPersonalError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invite_code_not_personal",
                "message": "该邀请码不可用作邀请人",
            },
        ) from e

    return InviteBindResponse(
        ok=True,
        referrer_user_id=result.referrer_user_id,
        referrer_invite_code=result.referrer_invite_code,
        bound_at_usage_count=result.new_usage_count,
    )
