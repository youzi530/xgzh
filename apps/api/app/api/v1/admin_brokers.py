"""Admin 券商管理路由 (Sprint 11 BE-S11-A03).

6 个 endpoint, 全部走 ``get_current_admin`` JWT + ``is_admin=true`` 鉴权:

| Method | Path                                | 用途                                |
|--------|-------------------------------------|-------------------------------------|
| GET    | /api/v1/admin/brokers               | 列表 (含下架 + 可选含软删)          |
| GET    | /api/v1/admin/brokers/{slug}        | 单券商详情 (含 partnership_*)       |
| POST   | /api/v1/admin/brokers               | 新建                                |
| PATCH  | /api/v1/admin/brokers/{slug}        | 编辑 (标量 set + JSONB merge)       |
| DELETE | /api/v1/admin/brokers/{slug}        | 软删 (deleted_at=now, is_active=0)  |
| POST   | /api/v1/admin/brokers/{slug}/restore | 恢复软删 (运维安全网, spec 未列但有)|

设计要点
========
- service 层抛业务 exception (``BrokerNotFoundError`` / ``BrokerSlugTakenError``),
  路由层 catch → HTTPException; 跟 ``admin_users.py`` 风格对齐
- PATCH JSONB 字段走 ``*_patch`` 后缀 (promotion_patch / fees_patch / features_patch):
  service 层做浅 merge, admin 改一个 key 不会把整个 JSONB 清空
- DELETE 软删幂等 (已删的再 DELETE 仍返 204, 不报错)
- 不挂 audit log — Sprint 11 Module E 加 ``admin_audit_service`` 后, 在本文件 wrap
  (audit 需要 request, service 不该感知 HTTP)

与 ``app/api/v1/brokers.py`` (用户视角) 的关系:
- brokers.py 走 ``@cached`` (TTL 600s), 用户不感知 admin 实时改动
- admin_brokers.py 写完立即 ``invalidate_namespace``, admin 下次 GET 拿新数据
- 用户侧 GET 在 cache 失效后下一次请求触发回源, 600s 内仍可能 stale (可接受)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.schemas.broker import (
    BrokerAdminDetail,
    BrokerAdminListResponse,
    BrokerCreate,
    BrokerUpdate,
)
from app.security.deps import get_current_admin
from app.services import broker_service
from app.services.broker_service import (
    BrokerNotFoundError,
    BrokerSlugTakenError,
)

router = APIRouter(prefix="/admin/brokers", tags=["admin"])


# ─── Helpers ──────────────────────────────────────────────────────────


def _not_found(slug: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "broker_not_found",
            "message": f"broker slug={slug!r} 不存在或已被永久删除",
        },
    )


# ─── 1. GET /admin/brokers — 列表 ────────────────────────────────────


@router.get(
    "",
    response_model=BrokerAdminListResponse,
    status_code=status.HTTP_200_OK,
    summary="管理员: 券商列表 (含下架; 可选含软删)",
    responses={
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
    },
)
async def list_brokers_admin(
    include_deleted: bool = Query(
        default=False, description="是否包含已软删的券商 (默认隐藏)"
    ),
    include_inactive: bool = Query(
        default=True, description="是否包含已下架 (is_active=False) 的券商 (admin 默认 true)"
    ),
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> BrokerAdminListResponse:
    items = await broker_service.admin_list_brokers(
        session, include_deleted=include_deleted, include_inactive=include_inactive
    )
    logger.info(
        f"admin.broker.list admin_id={admin.user_id} "
        f"include_deleted={include_deleted} include_inactive={include_inactive} "
        f"returned={len(items)}"
    )
    return BrokerAdminListResponse(
        items=[BrokerAdminDetail.model_validate(i) for i in items],
        total=len(items),
    )


# ─── 2. GET /admin/brokers/{slug} — 单券商详情 ──────────────────────


@router.get(
    "/{slug}",
    response_model=BrokerAdminDetail,
    status_code=status.HTTP_200_OK,
    summary="管理员: 单券商详情 (含 partnership_* + 软删标记)",
    responses={
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "slug 不存在"},
    },
)
async def get_broker_admin(
    slug: str,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> BrokerAdminDetail:
    try:
        payload = await broker_service.admin_get_broker(
            session, slug, include_deleted=True
        )
    except BrokerNotFoundError as e:
        raise _not_found(slug) from e
    logger.info(f"admin.broker.detail admin_id={admin.user_id} slug={slug}")
    return BrokerAdminDetail.model_validate(payload)


# ─── 3. POST /admin/brokers — 新建 ──────────────────────────────────


@router.post(
    "",
    response_model=BrokerAdminDetail,
    status_code=status.HTTP_201_CREATED,
    summary="管理员: 新建券商",
    responses={
        201: {"description": "新建成功"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        409: {"description": "slug 已被占用"},
        422: {"description": "字段校验失败 (Pydantic)"},
    },
)
async def create_broker_admin(
    body: BrokerCreate,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> BrokerAdminDetail:
    try:
        payload = await broker_service.create_broker(
            session,
            slug=body.slug,
            name_zh=body.name_zh,
            name_en=body.name_en,
            logo_url=body.logo_url,
            market_support=list(body.market_support),
            licenses=body.licenses,
            fees=body.fees,
            features=body.features,
            promotion=body.promotion,
            open_account_url=body.open_account_url,
            partnership_type=body.partnership_type,
            partnership_cpa_amount=body.partnership_cpa_amount,
            partnership_cps_rate=body.partnership_cps_rate,
            display_order=body.display_order,
            is_active=body.is_active,
        )
    except BrokerSlugTakenError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "broker_slug_taken",
                "message": f"slug={body.slug!r} 已被占用; 请换一个",
            },
        ) from e
    logger.warning(
        f"admin.broker.create.ok admin_id={admin.user_id} slug={body.slug} name={body.name_zh}"
    )
    return BrokerAdminDetail.model_validate(payload)


# ─── 4. PATCH /admin/brokers/{slug} — 编辑 ─────────────────────────


@router.patch(
    "/{slug}",
    response_model=BrokerAdminDetail,
    status_code=status.HTTP_200_OK,
    summary="管理员: 编辑券商 (标量 set + JSONB merge)",
    responses={
        200: {"description": "编辑成功 (空 patch 也 200, no-op)"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "slug 不存在或已软删 (恢复后再 PATCH)"},
        422: {"description": "字段校验失败"},
    },
)
async def update_broker_admin(
    slug: str,
    body: BrokerUpdate,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> BrokerAdminDetail:
    patch = body.model_dump(exclude_unset=True)
    try:
        payload = await broker_service.update_broker(
            session,
            slug=slug,
            **patch,
        )
    except BrokerNotFoundError as e:
        raise _not_found(slug) from e
    logger.warning(
        f"admin.broker.update.ok admin_id={admin.user_id} slug={slug} fields={list(patch.keys())}"
    )
    return BrokerAdminDetail.model_validate(payload)


# ─── 5. DELETE /admin/brokers/{slug} — 软删 ─────────────────────────


@router.delete(
    "/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="管理员: 软删券商 (deleted_at=now, is_active=False; 幂等)",
    responses={
        204: {"description": "软删成功 (已删的也 204)"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "slug 物理不存在"},
    },
)
async def soft_delete_broker_admin(
    slug: str,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        await broker_service.soft_delete_broker(session, slug=slug)
    except BrokerNotFoundError as e:
        raise _not_found(slug) from e
    logger.warning(f"admin.broker.delete.ok admin_id={admin.user_id} slug={slug}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─── 6. POST /admin/brokers/{slug}/restore — 恢复 ──────────────────


@router.post(
    "/{slug}/restore",
    response_model=BrokerAdminDetail,
    status_code=status.HTTP_200_OK,
    summary="管理员: 恢复软删的券商 (运维安全网, 幂等)",
    responses={
        200: {"description": "恢复成功 (没软删的也 200, no-op)"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "slug 物理不存在"},
    },
)
async def restore_broker_admin(
    slug: str,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> BrokerAdminDetail:
    try:
        payload = await broker_service.restore_broker(session, slug=slug)
    except BrokerNotFoundError as e:
        raise _not_found(slug) from e
    logger.warning(f"admin.broker.restore.ok admin_id={admin.user_id} slug={slug}")
    return BrokerAdminDetail.model_validate(payload)


__all__ = ["router"]
