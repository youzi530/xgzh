"""Admin 反馈管理路由 (Sprint 11 BE-S11-B02).

5 个 endpoint, 全部走 ``get_current_admin`` JWT + ``is_admin=true`` 鉴权:

| Method | Path                                       | 用途                              |
|--------|--------------------------------------------|-----------------------------------|
| GET    | /api/v1/admin/feedbacks                    | 列表 + 多维 filter + 分页         |
| GET    | /api/v1/admin/feedbacks/{feedback_id}      | 详情                              |
| PATCH  | /api/v1/admin/feedbacks/{feedback_id}      | 改 admin_status / admin_note      |
| DELETE | /api/v1/admin/feedbacks/{feedback_id}      | 软删 (deleted_at=now)             |
| POST   | /api/v1/admin/feedbacks/{feedback_id}/restore | 恢复软删 (运维安全网)         |

与老 ops 路径 (X-Admin-Token, ``GET /admin/feedbacks``) 的关系:
- 老路径在 ``admin.py`` 里保留, 行为不变 (limit/offset, 不过滤软删)
- 新路径在本文件, page/page_size, 默认隐藏软删, 加多维 filter + admin_status workflow
- 双系统并存 (Q4=A); ops 老脚本不用改

不暴露:
- 不能改 user 原文 ``content`` (PIPL 防篡改)
- 不能改 ``user_id`` / ``ip_inet`` (审计字段, 不可篡改)
- ``admin_note`` 不下发到用户 (反馈用户视角 endpoint 不返这个字段)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.schemas.feedback import (
    AdminFeedbackDetail,
    AdminFeedbackListItem,
    AdminFeedbackListResponse,
    AdminFeedbackStatus,
    AdminFeedbackUpdate,
    FeedbackCategory,
    FeedbackPlatform,
)
from app.security.deps import get_current_admin
from app.services import feedback_service
from app.services.admin_audit_service import (
    log_admin_action,
    resolve_request_context,
)
from app.services.feedback_service import FeedbackNotFoundError

router = APIRouter(prefix="/admin/feedbacks", tags=["admin"])


# ─── helpers ────────────────────────────────────────────────


def _to_detail(row) -> AdminFeedbackDetail:  # type: ignore[no-untyped-def]
    return AdminFeedbackDetail.model_validate(
        {
            **{
                k: getattr(row, k)
                for k in (
                    "feedback_id",
                    "user_id",
                    "category",
                    "content",
                    "contact",
                    "app_version",
                    "platform",
                    "ip_inet",
                    "created_at",
                    "admin_status",
                    "admin_note",
                    "reviewed_by",
                    "reviewed_at",
                    "deleted_at",
                )
            },
            "is_deleted": row.deleted_at is not None,
        }
    )


def _to_list_item(row) -> AdminFeedbackListItem:  # type: ignore[no-untyped-def]
    return AdminFeedbackListItem.model_validate(
        {
            **{
                k: getattr(row, k)
                for k in (
                    "feedback_id",
                    "user_id",
                    "category",
                    "content",
                    "contact",
                    "app_version",
                    "platform",
                    "ip_inet",
                    "created_at",
                    "admin_status",
                    "admin_note",
                    "reviewed_by",
                    "reviewed_at",
                    "deleted_at",
                )
            },
            "is_deleted": row.deleted_at is not None,
        }
    )


def _not_found(feedback_id: uuid.UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "feedback_not_found",
            "message": f"feedback {feedback_id} 不存在或已被永久删除",
        },
    )


# ─── 1. GET /admin/feedbacks — 列表 ─────────────────────────────


@router.get(
    "",
    response_model=AdminFeedbackListResponse,
    status_code=status.HTTP_200_OK,
    summary="管理员: 反馈列表 (含 admin workflow filter + 分页)",
    responses={
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
    },
)
async def list_feedbacks_admin(
    q: str | None = Query(default=None, max_length=200, description="content/contact 模糊搜"),
    category: FeedbackCategory | None = Query(default=None),
    platform: FeedbackPlatform | None = Query(default=None),
    admin_status: AdminFeedbackStatus | None = Query(default=None),
    include_deleted: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminFeedbackListResponse:
    items, total = await feedback_service.admin_list_feedbacks(
        session,
        q=q,
        category=category,
        platform=platform,
        admin_status=admin_status,
        include_deleted=include_deleted,
        page=page,
        page_size=page_size,
    )
    logger.info(
        f"admin.feedback.list admin_id={admin.user_id} q={q!r} category={category} "
        f"platform={platform} admin_status={admin_status} include_deleted={include_deleted} "
        f"page={page} returned={len(items)}/{total}"
    )
    return AdminFeedbackListResponse(
        items=[_to_list_item(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ─── 2. GET /admin/feedbacks/{id} — 详情 ────────────────────────


@router.get(
    "/{feedback_id}",
    response_model=AdminFeedbackDetail,
    status_code=status.HTTP_200_OK,
    summary="管理员: 单反馈详情 (含软删的)",
    responses={
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "feedback_id 不存在"},
    },
)
async def get_feedback_admin(
    feedback_id: uuid.UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminFeedbackDetail:
    try:
        row = await feedback_service.admin_get_feedback(
            session, feedback_id, include_deleted=True
        )
    except FeedbackNotFoundError as e:
        raise _not_found(feedback_id) from e
    logger.info(f"admin.feedback.detail admin_id={admin.user_id} id={feedback_id}")
    return _to_detail(row)


# ─── 3. PATCH /admin/feedbacks/{id} — 改 status / note ─────────


@router.patch(
    "/{feedback_id}",
    response_model=AdminFeedbackDetail,
    status_code=status.HTTP_200_OK,
    summary="管理员: 改处理状态 / 备注 (不能改 content / user_id / ip)",
    responses={
        200: {"description": "更新成功 (空 patch 也 200, no-op)"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "feedback_id 不存在或已软删"},
        422: {"description": "字段校验失败"},
    },
)
async def update_feedback_admin(
    feedback_id: uuid.UUID,
    body: AdminFeedbackUpdate,
    request: Request,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminFeedbackDetail:
    ip, ua = resolve_request_context(request)
    patch = body.model_dump(exclude_unset=True)
    try:
        row = await feedback_service.update_feedback(
            session,
            admin=admin,
            feedback_id=feedback_id,
            admin_status=patch.get("admin_status"),
            admin_note=patch.get("admin_note"),
        )
    except FeedbackNotFoundError as e:
        raise _not_found(feedback_id) from e
    await log_admin_action(
        admin_user_id=admin.user_id,
        action="update",
        target_type="feedback",
        target_id=str(feedback_id),
        changes={k: [None, v] for k, v in patch.items()},
        ip_inet=ip,
        user_agent=ua,
    )
    logger.warning(
        f"admin.feedback.update.ok admin_id={admin.user_id} id={feedback_id} fields={list(patch.keys())}"
    )
    return _to_detail(row)


# ─── 4. DELETE /admin/feedbacks/{id} — 软删 ────────────────────


@router.delete(
    "/{feedback_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="管理员: 软删反馈 (deleted_at=now; 幂等)",
    responses={
        204: {"description": "软删成功 (已删的也 204)"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "feedback_id 物理不存在"},
    },
)
async def soft_delete_feedback_admin(
    feedback_id: uuid.UUID,
    request: Request,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    ip, ua = resolve_request_context(request)
    try:
        await feedback_service.soft_delete_feedback(
            session, admin=admin, feedback_id=feedback_id
        )
    except FeedbackNotFoundError as e:
        raise _not_found(feedback_id) from e
    await log_admin_action(
        admin_user_id=admin.user_id,
        action="delete",
        target_type="feedback",
        target_id=str(feedback_id),
        ip_inet=ip,
        user_agent=ua,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─── 5. POST /admin/feedbacks/{id}/restore — 恢复 ──────────────


@router.post(
    "/{feedback_id}/restore",
    response_model=AdminFeedbackDetail,
    status_code=status.HTTP_200_OK,
    summary="管理员: 恢复软删反馈 (运维安全网, 幂等)",
    responses={
        200: {"description": "恢复成功 (没软删的也 200, no-op)"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "feedback_id 物理不存在"},
    },
)
async def restore_feedback_admin(
    feedback_id: uuid.UUID,
    request: Request,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminFeedbackDetail:
    ip, ua = resolve_request_context(request)
    try:
        row = await feedback_service.restore_feedback(
            session, admin=admin, feedback_id=feedback_id
        )
    except FeedbackNotFoundError as e:
        raise _not_found(feedback_id) from e
    await log_admin_action(
        admin_user_id=admin.user_id,
        action="restore",
        target_type="feedback",
        target_id=str(feedback_id),
        ip_inet=ip,
        user_agent=ua,
    )
    return _to_detail(row)


__all__ = ["router"]
