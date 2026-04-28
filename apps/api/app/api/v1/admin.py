"""OPS-S4-001 + BE-S5-002 + BE-S5-004 Admin API: 灰度旋钮 + 错误率监控 + 反馈管理 + PIPL PII 审计.

路由总览 (全部走 ``X-Admin-Token`` header 鉴权, 见 ``security/admin.py``):

| Method | Path                          | 用途                                |
|--------|-------------------------------|-------------------------------------|
| GET    | /api/v1/admin/flags           | 列所有 flag 配置                    |
| GET    | /api/v1/admin/flags/{name}    | 查单 flag                           |
| PUT    | /api/v1/admin/flags/{name}    | 写 / 改 flag (admin-write)          |
| DELETE | /api/v1/admin/flags/{name}    | 删 flag                             |
| GET    | /api/v1/admin/metrics         | 当前窗口 错误率 / total / errors    |
| POST   | /api/v1/admin/metrics/reset   | 清当前窗口计数 (debug / 灰度回滚后) |
| GET    | /api/v1/admin/feedbacks       | 反馈列表 (分页 + filter)            |
| GET    | /api/v1/admin/pii-inventory   | PIPL 个人信息收集清单 + 实时计数    |

注意:
- 所有路由都 ``require_admin_token`` Depends, ``OPS_ADMIN_TOKEN`` 留空时返 503
- ``flags`` 与 ``metrics`` 的语义都是"运维触达, 不在用户产品里出现"; 不在 OpenAPI
  schema 上设 ``include_in_schema=False`` 是为了让 ops 的同事能从 ``/docs`` 直接试,
  生产实际靠"未配 OPS_ADMIN_TOKEN → 503"做最后保险。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas.feedback import (
    FeedbackAdminItem,
    FeedbackAdminListResponse,
    FeedbackCategory,
    FeedbackPlatform,
)
from app.schemas.pii_inventory import PIIInventoryResponse
from app.security.admin import require_admin_token
from app.services import (
    error_monitor,
    feature_flags,
    feedback_service,
    pii_inventory_service,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# ─── Schemas ──────────────────────────────────────────────────────


class FlagPayload(BaseModel):
    name: str
    enabled: bool
    rollout_pct: int = Field(ge=0, le=100)
    updated_at: str


class FlagListResponse(BaseModel):
    flags: list[FlagPayload]


class FlagWriteRequest(BaseModel):
    enabled: bool
    rollout_pct: int = Field(ge=0, le=100)


class MetricsPayload(BaseModel):
    window_seconds: int
    total_requests: int
    total_errors: int
    error_pct: float


# ─── Flags 路由 ────────────────────────────────────────────────────


@router.get(
    "/flags",
    response_model=FlagListResponse,
    dependencies=[Depends(require_admin_token)],
)
async def list_flags() -> FlagListResponse:
    """列所有 flag 配置."""
    flags = await feature_flags.list_flags()
    return FlagListResponse(
        flags=[FlagPayload(**f.to_dict()) for f in flags]
    )


@router.get(
    "/flags/{name}",
    response_model=FlagPayload,
    dependencies=[Depends(require_admin_token)],
)
async def get_flag(name: str) -> FlagPayload:
    flag = await feature_flags.get_flag(name)
    if flag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "flag_not_found", "message": f"flag '{name}' 未注册"},
        )
    return FlagPayload(**flag.to_dict())


@router.put(
    "/flags/{name}",
    response_model=FlagPayload,
    dependencies=[Depends(require_admin_token)],
)
async def upsert_flag(name: str, payload: FlagWriteRequest) -> FlagPayload:
    """写 / 改 flag (创建 + 更新一栈, 走 PUT 幂等语义)."""
    cfg = await feature_flags.set_flag(
        name,
        enabled=payload.enabled,
        rollout_pct=payload.rollout_pct,
    )
    return FlagPayload(**cfg.to_dict())


@router.delete(
    "/flags/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin_token)],
)
async def delete_flag(name: str) -> None:
    deleted = await feature_flags.delete_flag(name)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "flag_not_found", "message": f"flag '{name}' 未注册"},
        )


# ─── Metrics 路由 ──────────────────────────────────────────────────


@router.get(
    "/metrics",
    response_model=MetricsPayload,
    dependencies=[Depends(require_admin_token)],
)
async def get_metrics() -> MetricsPayload:
    metrics = await error_monitor.get_metrics()
    return MetricsPayload(**metrics.as_dict())


@router.post(
    "/metrics/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin_token)],
)
async def reset_metrics() -> None:
    await error_monitor.reset_metrics()


# ─── Feedbacks 路由 (BE-S5-004) ────────────────────────────────────


@router.get(
    "/feedbacks",
    response_model=FeedbackAdminListResponse,
    dependencies=[Depends(require_admin_token)],
    summary="拉反馈列表 (admin)",
)
async def list_feedbacks(
    category: FeedbackCategory | None = Query(
        default=None,
        description="bug / feature / content / other",
    ),
    platform: FeedbackPlatform | None = Query(
        default=None,
        description="h5 / mp-weixin / app-android / app-ios",
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> FeedbackAdminListResponse:
    """admin 拉反馈, 分页 + 可选 category / platform filter."""
    items, total = await feedback_service.list_feedbacks(
        session,
        category=category,
        platform=platform,
        limit=limit,
        offset=offset,
    )
    return FeedbackAdminListResponse(
        items=[FeedbackAdminItem.model_validate(it) for it in items],
        total=total,
        limit=limit,
        offset=offset,
    )


# ─── PIPL PII 审计 (BE-S5-002) ─────────────────────────────────────


@router.get(
    "/pii-inventory",
    response_model=PIIInventoryResponse,
    dependencies=[Depends(require_admin_token)],
    summary="PIPL 个人信息收集清单 + 实时数据规模 (合规审计)",
)
async def get_pii_inventory(
    session: AsyncSession = Depends(get_session),
) -> PIIInventoryResponse:
    """返回静态 PII 字段清单 + DB 实时行数 + 第三方 SDK + 同意机制 + 出境法域.

    用途:
    - PIPL 合规审计 (法务 / 监管下载)
    - 配合 BE-S5-003 注销账号: 清单字段 = 注销时必清字段
    - 让前端拉清单生成"用户协议 → 我们收集的个人信息"章节
    """
    payload = await pii_inventory_service.build_inventory_response(session)
    return PIIInventoryResponse.model_validate(payload)


__all__ = ["router"]
