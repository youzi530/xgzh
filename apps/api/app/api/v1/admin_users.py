"""Admin 用户管理路由 (Sprint 10 BE-S10-004 / BE-S10-005).

5 个 endpoint, 全部走 ``get_current_admin`` JWT + ``is_admin=true`` 鉴权:

| Method | Path                              | 用途                              |
|--------|-----------------------------------|-----------------------------------|
| GET    | /api/v1/admin/users               | 列表 + 搜索 + 分页                |
| GET    | /api/v1/admin/users/{user_id}     | 单用户详情 (含 VIP / 邀请数)      |
| PATCH  | /api/v1/admin/users/{user_id}     | 编辑 nickname / region / status   |
| DELETE | /api/v1/admin/users/{user_id}     | 软删 (deleted_at=now, status=0)   |
| POST   | /api/v1/admin/users/{user_id}/grant-vip | 加 VIP 时长 (1-365 d)       |

与 ``app/api/v1/admin.py`` 的关系:
- ``admin.py`` 走 ``X-Admin-Token`` (ops 通道, 老路径)
- 本文件走 ``get_current_admin`` (JWT in-app, 新路径)
- 两者并存; 用户拍板 Q4=A 不冲突

返回字段策略:
- 列表 ``AdminUserListItem``: phone/email 都脱敏返出 — admin 排查需要看清是哪个手机/邮箱
  对应哪个用户, 但仍走 ``mask_phone``/``mask_email`` 防截图泄露 (PIPL §22 最小化精神)
- 详情 ``AdminUserDetail``: 同上脱敏 + 额外暴露 vip_total_paid_cny / invite_count
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.schemas.admin_users import (
    AdminUserDetail,
    AdminUserListItem,
    AdminUserListResponse,
    AdminUserUpdate,
    GrantVipRequest,
)
from app.security.deps import get_current_admin
from app.services import admin_user_service
from app.services.admin_user_service import (
    CannotDeleteSelfError,
    CannotDemoteSelfError,
    UserNotFoundError,
    _AggRow,
)
from app.utils.email import mask_email
from app.utils.phone import mask_phone

router = APIRouter(prefix="/admin/users", tags=["admin"])


# ─── Helper: AggRow → schema ──────────────────────────────────────


def _to_list_item(row: _AggRow) -> AdminUserListItem:
    """聚合行 → 列表项 schema. phone/email 脱敏后填入."""
    user = row.user
    return AdminUserListItem.model_validate(
        {
            "user_id": user.user_id,
            "phone_masked": mask_phone(user.phone) if user.phone else None,
            "email_masked": mask_email(user.email) if user.email else None,
            "nickname": user.nickname,
            "avatar_url": user.avatar_url,
            "region": user.region,
            "is_admin": user.is_admin,
            "status": user.status,
            "is_deleted": user.deleted_at is not None,
            "vip_status": row.vip_status,
            "vip_end_at": row.vip_end_at,
            "created_at": user.created_at,
        }
    )


def _to_detail(row: _AggRow) -> AdminUserDetail:
    """聚合行 → 详情 schema. 比列表多 invite_count + vip 字段全集."""
    user = row.user
    return AdminUserDetail.model_validate(
        {
            "user_id": user.user_id,
            "phone_masked": mask_phone(user.phone) if user.phone else None,
            "email_masked": mask_email(user.email) if user.email else None,
            "nickname": user.nickname,
            "avatar_url": user.avatar_url,
            "region": user.region,
            "invite_code": user.invite_code,
            "invited_by_user_id": user.invited_by,
            "is_admin": user.is_admin,
            "status": user.status,
            "is_deleted": user.deleted_at is not None,
            "deleted_at": user.deleted_at,
            "last_active_at": user.last_active_at,
            "created_at": user.created_at,
            "invite_count": row.invite_count,
            "vip_status": row.vip_status,
            "vip_plan": row.vip_plan,
            "vip_start_at": row.vip_start_at,
            "vip_end_at": row.vip_end_at,
            "vip_total_paid_cny": (
                f"{row.vip_total_paid_cny:.2f}" if row.vip_total_paid_cny is not None else None
            ),
        }
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "user_not_found", "message": "目标用户不存在或已彻底删除"},
    )


# ─── 1. GET /admin/users — 列表 + 搜索 ────────────────────────────


@router.get(
    "",
    response_model=AdminUserListResponse,
    status_code=status.HTTP_200_OK,
    summary="管理员: 用户列表 (含搜索 + 分页)",
    responses={
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
    },
)
async def list_users(
    q: str | None = Query(
        default=None,
        max_length=64,
        description="搜索关键词: 手机号/邮箱/昵称 ilike 模糊匹配 (大小写不敏感); 不填返全列表",
    ),
    is_admin_filter: bool | None = Query(
        default=None,
        alias="is_admin",
        description="仅返管理员 (true) / 仅返普通用户 (false) / 全返 (不传)",
    ),
    include_deleted: bool = Query(
        default=False,
        description="是否包含已软删用户 (deleted_at NOT NULL); 默认 false",
    ),
    page: int = Query(default=1, ge=1, description="页码 (1-based)"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数 (1-100)"),
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserListResponse:
    items, total = await admin_user_service.list_users_with_aggregate(
        session,
        q=q,
        is_admin=is_admin_filter,
        include_deleted=include_deleted,
        page=page,
        page_size=page_size,
    )
    logger.info(
        f"admin.user.list admin_id={admin.user_id} q={q!r} "
        f"is_admin={is_admin_filter} include_deleted={include_deleted} "
        f"page={page} page_size={page_size} returned={len(items)}/{total}"
    )
    return AdminUserListResponse(
        items=[_to_list_item(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ─── 2. GET /admin/users/{user_id} — 单用户详情 ───────────────────


@router.get(
    "/{user_id}",
    response_model=AdminUserDetail,
    status_code=status.HTTP_200_OK,
    summary="管理员: 单用户详情 (含 VIP / 邀请数 / 软删状态)",
    responses={
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "目标用户不存在"},
    },
)
async def get_user(
    user_id: uuid.UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserDetail:
    try:
        row = await admin_user_service.get_user_aggregate(
            session, user_id, include_deleted=True
        )
    except UserNotFoundError as e:
        raise _not_found() from e
    return _to_detail(row)


# ─── 3. PATCH /admin/users/{user_id} — 编辑 ──────────────────────


@router.patch(
    "/{user_id}",
    response_model=AdminUserDetail,
    status_code=status.HTTP_200_OK,
    summary="管理员: 编辑 nickname / region / status",
    responses={
        400: {"description": "字段全空"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员; 或试图修改自己的 status"},
        404: {"description": "目标用户不存在"},
    },
)
async def update_user(
    user_id: uuid.UUID,
    body: AdminUserUpdate,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserDetail:
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "no_change", "message": "请求未指定要修改的字段"},
        )
    try:
        row = await admin_user_service.patch_user(
            session,
            admin=admin,
            target_user_id=user_id,
            nickname=patch.get("nickname"),
            region=patch.get("region"),
            status_val=patch.get("status"),
        )
    except UserNotFoundError as e:
        raise _not_found() from e
    except CannotDemoteSelfError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "cannot_demote_self",
                "message": "管理员不能修改自己的账户状态; 请由其他管理员操作",
            },
        ) from e
    return _to_detail(row)


# ─── 4. DELETE /admin/users/{user_id} — 软删 ─────────────────────


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="管理员: 软删用户 (deleted_at=now, status=0, 强制下线)",
    responses={
        204: {"description": "软删成功; 已删则视为幂等成功"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员; 或试图删自己"},
        404: {"description": "目标用户不存在"},
    },
)
async def delete_user(
    user_id: uuid.UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        await admin_user_service.soft_delete_user_by_admin(
            session, admin=admin, target_user_id=user_id
        )
    except UserNotFoundError as e:
        raise _not_found() from e
    except CannotDeleteSelfError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "cannot_delete_self",
                "message": "管理员不能删除自己; 请由其他管理员操作",
            },
        ) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─── 5. POST /admin/users/{user_id}/grant-vip — 加 VIP 时长 ─────


@router.post(
    "/{user_id}/grant-vip",
    response_model=AdminUserDetail,
    status_code=status.HTTP_200_OK,
    summary="管理员: 给用户加 VIP 时长 (1-365 d; 非幂等)",
    responses={
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "目标用户不存在或已软删 (软删用户禁加 VIP)"},
        422: {"description": "days 超 365 / reason 太短 (Pydantic)"},
    },
)
async def grant_vip(
    user_id: uuid.UUID,
    body: GrantVipRequest,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserDetail:
    try:
        row = await admin_user_service.grant_vip_to_user(
            session,
            admin=admin,
            target_user_id=user_id,
            days=body.days,
            reason=body.reason,
        )
    except UserNotFoundError as e:
        raise _not_found() from e
    return _to_detail(row)


__all__ = ["router"]
