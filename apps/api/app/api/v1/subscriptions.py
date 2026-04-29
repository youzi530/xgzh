"""中签记账路由 (Sprint 6 BE-S6-002).

全部端点要求登录 (``get_current_user``):
- 账户 4 个端点 (``/subscriptions/accounts``)
- 中签 records 5 个端点 (``/subscriptions``)

错误映射:
- :class:`SubscriptionNotFoundError` → 404
- :class:`SubscriptionConflictError` → 409 (label 重名)
- :class:`RateLimitExceeded` → 429 (main.py 全局 handler)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.schemas.subscription import (
    SubscriptionAccountCreateRequest,
    SubscriptionAccountListResponse,
    SubscriptionAccountResponse,
    SubscriptionAccountUpdateRequest,
    SubscriptionRecordCreateRequest,
    SubscriptionRecordListResponse,
    SubscriptionRecordResponse,
    SubscriptionRecordUpdateRequest,
    SubscriptionRegion,
)
from app.security import get_current_user
from app.services import subscription_service
from app.services.subscription_service import (
    SubscriptionConflictError,
    SubscriptionNotFoundError,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

# ─── 账户端点 ────────────────────────────────────────────────────────────


@router.post(
    "/accounts",
    response_model=SubscriptionAccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建中签记账账户",
    responses={
        409: {"description": "账户名已存在 (UNIQUE user_id, label)"},
        429: {"description": "提交过于频繁 (60s ≤ 5)"},
    },
)
async def create_account(
    req: SubscriptionAccountCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionAccountResponse:
    await subscription_service.enforce_create_account_rate_limit(user_id=user.user_id)
    try:
        row = await subscription_service.create_account(
            session,
            user_id=user.user_id,
            label=req.label,
            broker_name=req.broker_name,
            region=req.region,
            is_primary=req.is_primary,
        )
    except SubscriptionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    await session.commit()
    return SubscriptionAccountResponse.model_validate(row)


@router.get(
    "/accounts",
    response_model=SubscriptionAccountListResponse,
    summary="列出我的中签账户",
)
async def list_accounts(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionAccountListResponse:
    rows = await subscription_service.list_accounts(session, user_id=user.user_id)
    return SubscriptionAccountListResponse(
        items=[SubscriptionAccountResponse.model_validate(r) for r in rows],
        total=len(rows),
    )


@router.put(
    "/accounts/{account_id}",
    response_model=SubscriptionAccountResponse,
    summary="改账户 (partial)",
    responses={
        404: {"description": "账户不存在或不属于本人"},
        409: {"description": "新 label 重名"},
    },
)
async def update_account(
    account_id: uuid.UUID,
    req: SubscriptionAccountUpdateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionAccountResponse:
    try:
        row = await subscription_service.update_account(
            session,
            user_id=user.user_id,
            account_id=account_id,
            label=req.label,
            broker_name=req.broker_name,
            region=req.region,
            is_primary=req.is_primary,
        )
    except SubscriptionNotFoundError as e:
        raise HTTPException(status_code=404, detail="account_not_found") from e
    except SubscriptionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    await session.commit()
    return SubscriptionAccountResponse.model_validate(row)


@router.delete(
    "/accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删账户 (级联删 records)",
    responses={404: {"description": "账户不存在或不属于本人"}},
)
async def delete_account(
    account_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await subscription_service.delete_account(
            session, user_id=user.user_id, account_id=account_id
        )
    except SubscriptionNotFoundError as e:
        raise HTTPException(status_code=404, detail="account_not_found") from e
    await session.commit()


# ─── 中签 records 端点 ──────────────────────────────────────────────────


@router.post(
    "",
    response_model=SubscriptionRecordResponse,
    status_code=status.HTTP_201_CREATED,
    summary="录一条中签记录",
    responses={
        404: {"description": "account_id 不存在或不属于本人"},
        429: {"description": "提交过于频繁 (60s ≤ 10)"},
    },
)
async def create_record(
    req: SubscriptionRecordCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionRecordResponse:
    await subscription_service.enforce_create_record_rate_limit(user_id=user.user_id)
    try:
        row = await subscription_service.create_record(
            session,
            user_id=user.user_id,
            account_id=req.account_id,
            ipo_code=req.ipo_code,
            ipo_name=req.ipo_name,
            region=req.region,
            subscribe_shares=req.subscribe_shares,
            allotted_shares=req.allotted_shares,
            subscribe_price=req.subscribe_price,
            margin_amount=req.margin_amount,
            fees=req.fees,
            first_day_close=req.first_day_close,
            sell_price=req.sell_price,
            sell_at=req.sell_at,
            notes=req.notes,
            subscribed_at=req.subscribed_at,
            listed_at=req.listed_at,
        )
    except SubscriptionNotFoundError as e:
        raise HTTPException(status_code=404, detail="account_not_found") from e
    await session.commit()
    return SubscriptionRecordResponse.model_validate(row)


@router.get(
    "",
    response_model=SubscriptionRecordListResponse,
    summary="列我的中签记录 (按 listed_at desc, NULL 末尾)",
)
async def list_records(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    account_id: uuid.UUID | None = None,
    region: SubscriptionRegion | None = None,
    limit: int = 20,
    offset: int = 0,
) -> SubscriptionRecordListResponse:
    if limit > 100:
        limit = 100
    if offset < 0:
        offset = 0
    rows, total = await subscription_service.list_records(
        session,
        user_id=user.user_id,
        account_id=account_id,
        region=region,
        limit=limit,
        offset=offset,
    )
    return SubscriptionRecordListResponse(
        items=[SubscriptionRecordResponse.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{record_id}",
    response_model=SubscriptionRecordResponse,
    summary="读单条中签详情",
    responses={404: {"description": "record 不存在或不属于本人"}},
)
async def get_record(
    record_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionRecordResponse:
    try:
        row = await subscription_service.get_record(
            session, user_id=user.user_id, record_id=record_id
        )
    except SubscriptionNotFoundError as e:
        raise HTTPException(status_code=404, detail="record_not_found") from e
    return SubscriptionRecordResponse.model_validate(row)


@router.put(
    "/{record_id}",
    response_model=SubscriptionRecordResponse,
    summary="改中签记录 (partial; PnL 自动重算)",
    responses={404: {"description": "record / 新 account_id 不存在或不属于本人"}},
)
async def update_record(
    record_id: uuid.UUID,
    req: SubscriptionRecordUpdateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionRecordResponse:
    try:
        row = await subscription_service.update_record(
            session,
            user_id=user.user_id,
            record_id=record_id,
            account_id=req.account_id,
            ipo_code=req.ipo_code,
            ipo_name=req.ipo_name,
            region=req.region,
            subscribe_shares=req.subscribe_shares,
            allotted_shares=req.allotted_shares,
            subscribe_price=req.subscribe_price,
            margin_amount=req.margin_amount,
            fees=req.fees,
            first_day_close=req.first_day_close,
            sell_price=req.sell_price,
            sell_at=req.sell_at,
            notes=req.notes,
            subscribed_at=req.subscribed_at,
            listed_at=req.listed_at,
        )
    except SubscriptionNotFoundError as e:
        raise HTTPException(status_code=404, detail="not_found") from e
    await session.commit()
    return SubscriptionRecordResponse.model_validate(row)


@router.delete(
    "/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删中签记录",
    responses={404: {"description": "record 不存在或不属于本人"}},
)
async def delete_record(
    record_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await subscription_service.delete_record(
            session, user_id=user.user_id, record_id=record_id
        )
    except SubscriptionNotFoundError as e:
        raise HTTPException(status_code=404, detail="record_not_found") from e
    await session.commit()
