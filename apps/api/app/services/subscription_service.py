"""中签记账业务服务 (Sprint 6 BE-S6-002).

接口:
- 账户:
  - :func:`create_account` / :func:`list_accounts` / :func:`update_account` / :func:`delete_account`
- 中签 records:
  - :func:`create_record` / :func:`list_records` / :func:`get_record`
  - :func:`update_record` / :func:`delete_record`
- 限流:
  - :func:`enforce_create_account_rate_limit`  60s ≤ 5 / user
  - :func:`enforce_create_record_rate_limit`   60s ≤ 10 / user

设计要点
========

1. **跨用户访问统一 raise NotFound**: 不泄露 record / account 存在性 (spec/13 AC).
   service 层调用方 (router) catch ``SubscriptionNotFoundError`` → 返 404.
2. **PnL BE 算后存盘** (不用 generated column): 公式
   - ``unrealized_pnl = (first_day_close - subscribe_price) * allotted_shares - fees - margin_amount``
   - ``realized_pnl   = (sell_price       - subscribe_price) * allotted_shares - fees - margin_amount``
   缺任一关键字段 (subscribe_price / first_day_close / sell_price) 时对应 PnL = NULL.
   margin_amount NULL 视为 0; fees 默认 0 (NOT NULL DB 层).
3. **MVP 不查 ipos 表回填**: 用户主流程是"在券商 APP 看到中签后手动录入到 XGZH",
   ipo_name / listed_at / first_day_close 等都让用户从券商截图复制. 自动回填(查
   ipos 表自动取 listing_date / first_day_change_pct → close) 留 BE-S6-002.1 优化.
4. **是否唯一主账户**: ``is_primary`` 不在 DB 强约束 (partial unique 在 alembic 维护
   成本高). service 层在 ``create_account`` / ``update_account`` 时, 如果新值
   ``is_primary=True``, 把同 user 其它账户的 ``is_primary`` 全部置 false (单 SQL UPDATE).
5. **删账户级联删 records**: DB 层 ON DELETE CASCADE 保证, service 层不必显式删.
   但 router 应给 UI 二次确认 (前端弹 modal).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import RateLimitExceeded, get_redis_client
from app.db.models import SubscriptionAccount, SubscriptionRecord

# 限流配额 (spec/13 §BE-S6-002 AC)
_ACCOUNT_RATE_TIMES = 5
_ACCOUNT_RATE_WINDOW = 60
_RECORD_RATE_TIMES = 10
_RECORD_RATE_WINDOW = 60


class SubscriptionNotFoundError(Exception):
    """记录 / 账户不存在或不属于该用户 — service 层抛, router 转 404."""


class SubscriptionConflictError(Exception):
    """记录 / 账户冲突 (e.g. label 重名) — service 层抛, router 转 409."""


# ─── 限流 ────────────────────────────────────────────────────────────────


async def enforce_create_account_rate_limit(*, user_id: uuid.UUID) -> None:
    """``POST /api/v1/subscriptions/accounts`` 限流 60s ≤ 5 / user."""
    client = get_redis_client()
    key = f"rate:sub_account_create:user:{user_id}"
    current = await client.incr_with_expire(key, _ACCOUNT_RATE_WINDOW)
    if current > _ACCOUNT_RATE_TIMES:
        ttl = await client.ttl(key)
        retry_after = ttl if ttl > 0 else _ACCOUNT_RATE_WINDOW
        logger.info(
            f"sub_account.rate_limit_exceeded user={user_id} current={current}"
        )
        raise RateLimitExceeded(
            key=key,
            times=_ACCOUNT_RATE_TIMES,
            per_seconds=_ACCOUNT_RATE_WINDOW,
            retry_after=retry_after,
        )


async def enforce_create_record_rate_limit(*, user_id: uuid.UUID) -> None:
    """``POST /api/v1/subscriptions`` 限流 60s ≤ 10 / user."""
    client = get_redis_client()
    key = f"rate:sub_record_create:user:{user_id}"
    current = await client.incr_with_expire(key, _RECORD_RATE_WINDOW)
    if current > _RECORD_RATE_TIMES:
        ttl = await client.ttl(key)
        retry_after = ttl if ttl > 0 else _RECORD_RATE_WINDOW
        logger.info(
            f"sub_record.rate_limit_exceeded user={user_id} current={current}"
        )
        raise RateLimitExceeded(
            key=key,
            times=_RECORD_RATE_TIMES,
            per_seconds=_RECORD_RATE_WINDOW,
            retry_after=retry_after,
        )


# ─── 账户 CRUD ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _AccountUpdate:
    """update_account 内部使用的字段 patch dict."""

    label: str | None = None
    broker_name: str | None = None
    region: str | None = None
    is_primary: bool | None = None


async def _clear_other_primary(
    session: AsyncSession, *, user_id: uuid.UUID, exclude_id: uuid.UUID | None = None
) -> None:
    """把同 user 其它账户的 is_primary 置 false. 单 SQL 执行."""
    stmt = (
        update(SubscriptionAccount)
        .where(SubscriptionAccount.user_id == user_id)
        .values(is_primary=False)
    )
    if exclude_id is not None:
        stmt = stmt.where(SubscriptionAccount.id != exclude_id)
    await session.execute(stmt)


async def create_account(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    label: str,
    broker_name: str | None,
    region: str,
    is_primary: bool,
) -> SubscriptionAccount:
    """创建账户. ``label`` 重名 raise :class:`SubscriptionConflictError`."""
    if is_primary:
        # 把现有主账户置 false (互斥)
        await _clear_other_primary(session, user_id=user_id)

    row = SubscriptionAccount(
        user_id=user_id,
        label=label,
        broker_name=broker_name,
        region=region,
        is_primary=is_primary,
    )
    session.add(row)
    try:
        await session.flush()
        await session.refresh(row)
    except IntegrityError as e:
        await session.rollback()
        # uq_sub_accounts_user_label 冲突
        if "uq_sub_accounts_user_label" in str(e.orig):
            raise SubscriptionConflictError(
                f"账户名 '{label}' 已存在"
            ) from e
        raise

    logger.info(
        f"sub_account.created user={user_id} id={row.id} label={label} "
        f"region={region} is_primary={is_primary}"
    )
    return row


async def list_accounts(
    session: AsyncSession, *, user_id: uuid.UUID
) -> list[SubscriptionAccount]:
    """列出该 user 全部账户; 主账户排在最前, 其余按 created_at desc."""
    stmt = (
        select(SubscriptionAccount)
        .where(SubscriptionAccount.user_id == user_id)
        .order_by(
            SubscriptionAccount.is_primary.desc(),
            SubscriptionAccount.created_at.desc(),
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def get_account(
    session: AsyncSession, *, user_id: uuid.UUID, account_id: uuid.UUID
) -> SubscriptionAccount:
    """读单账户; 不属于本人 raise NotFound."""
    stmt = select(SubscriptionAccount).where(
        SubscriptionAccount.id == account_id,
        SubscriptionAccount.user_id == user_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise SubscriptionNotFoundError(f"account {account_id} not found")
    return row


async def update_account(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    label: str | None = None,
    broker_name: str | None = None,
    region: str | None = None,
    is_primary: bool | None = None,
) -> SubscriptionAccount:
    """改账户. partial 字段; ``label`` 重名抛 :class:`SubscriptionConflictError`."""
    account = await get_account(session, user_id=user_id, account_id=account_id)

    if is_primary is True:
        # 切换主账户: 排除自身的 is_primary 全置 false
        await _clear_other_primary(session, user_id=user_id, exclude_id=account_id)

    if label is not None:
        account.label = label
    # broker_name 显式传入即更新 (含传 None 表示清空); 这里区分"未传字段" 与"传 None":
    # router 层 schema 用 default=None 没区分, 与 feedback / vip 一致只支持非 None 更新.
    # broker_name 想清空可传空字符串 ""  → service 层不区分, DB 存 ""
    if broker_name is not None:
        account.broker_name = broker_name
    if region is not None:
        account.region = region
    if is_primary is not None:
        account.is_primary = is_primary

    try:
        await session.flush()
        await session.refresh(account)
    except IntegrityError as e:
        await session.rollback()
        if "uq_sub_accounts_user_label" in str(e.orig):
            raise SubscriptionConflictError(
                f"账户名 '{label}' 已存在"
            ) from e
        raise

    logger.info(f"sub_account.updated user={user_id} id={account_id}")
    return account


async def delete_account(
    session: AsyncSession, *, user_id: uuid.UUID, account_id: uuid.UUID
) -> None:
    """删账户. 级联删 records (DB ON DELETE CASCADE). 不属于本人 raise NotFound."""
    account = await get_account(session, user_id=user_id, account_id=account_id)
    await session.delete(account)
    await session.flush()
    logger.info(f"sub_account.deleted user={user_id} id={account_id}")


# ─── PnL 计算 ────────────────────────────────────────────────────────────


def _compute_pnl(
    *,
    subscribe_price: Decimal | None,
    first_day_close: Decimal | None,
    sell_price: Decimal | None,
    allotted_shares: int,
    fees: Decimal,
    margin_amount: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    """算 (realized_pnl, unrealized_pnl).

    缺任一关键字段 → 该侧 NULL. 公式见模块 docstring.
    """
    if allotted_shares <= 0 or subscribe_price is None:
        # 未中签 (allotted=0) 或没成本基准 → PnL 都 NULL
        return None, None

    margin: Decimal = margin_amount if margin_amount is not None else Decimal("0")
    base_cost = fees + margin  # 不可避免成本 (中签后无论卖不卖都付)

    unrealized: Decimal | None = None
    if first_day_close is not None:
        unrealized = (
            (first_day_close - subscribe_price) * Decimal(allotted_shares) - base_cost
        )
        unrealized = unrealized.quantize(Decimal("0.01"))

    realized: Decimal | None = None
    if sell_price is not None:
        realized = (
            (sell_price - subscribe_price) * Decimal(allotted_shares) - base_cost
        )
        realized = realized.quantize(Decimal("0.01"))

    return realized, unrealized


# ─── 中签 records CRUD ───────────────────────────────────────────────────


async def _verify_account_owned(
    session: AsyncSession, *, user_id: uuid.UUID, account_id: uuid.UUID
) -> SubscriptionAccount:
    """验账户存在 + 属于本人; 否则 raise NotFound."""
    return await get_account(session, user_id=user_id, account_id=account_id)


async def create_record(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    ipo_code: str,
    ipo_name: str | None,
    region: str,
    subscribe_shares: int,
    allotted_shares: int,
    subscribe_price: Decimal | None,
    margin_amount: Decimal | None,
    fees: Decimal,
    first_day_close: Decimal | None,
    sell_price: Decimal | None,
    sell_at: datetime | None,
    notes: str | None,
    subscribed_at: date,
    listed_at: date | None,
) -> SubscriptionRecord:
    """落 PG 一条中签 record. PnL 由 service 算后存."""
    # 验账户属于本人
    await _verify_account_owned(session, user_id=user_id, account_id=account_id)

    realized, unrealized = _compute_pnl(
        subscribe_price=subscribe_price,
        first_day_close=first_day_close,
        sell_price=sell_price,
        allotted_shares=allotted_shares,
        fees=fees,
        margin_amount=margin_amount,
    )

    row = SubscriptionRecord(
        user_id=user_id,
        account_id=account_id,
        ipo_code=ipo_code,
        ipo_name=ipo_name,
        region=region,
        subscribe_shares=subscribe_shares,
        allotted_shares=allotted_shares,
        subscribe_price=subscribe_price,
        margin_amount=margin_amount,
        fees=fees,
        first_day_close=first_day_close,
        sell_price=sell_price,
        sell_at=sell_at,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        notes=notes,
        subscribed_at=subscribed_at,
        listed_at=listed_at,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)

    logger.info(
        f"sub_record.created user={user_id} id={row.id} ipo_code={ipo_code} "
        f"allotted={allotted_shares} unrealized_pnl={unrealized} realized_pnl={realized}"
    )
    return row


async def list_records(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    account_id: uuid.UUID | None = None,
    region: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[SubscriptionRecord], int]:
    """列 user 中签 records, 默认按 listed_at DESC NULLS LAST 排序.

    返 (items, total).
    """
    base_filters = [SubscriptionRecord.user_id == user_id]
    if account_id is not None:
        base_filters.append(SubscriptionRecord.account_id == account_id)
    if region is not None:
        base_filters.append(SubscriptionRecord.region == region)

    count_stmt = select(func.count()).select_from(SubscriptionRecord)
    list_stmt = select(SubscriptionRecord).order_by(
        SubscriptionRecord.listed_at.desc().nulls_last(),
        SubscriptionRecord.subscribed_at.desc(),
    )
    for f in base_filters:
        count_stmt = count_stmt.where(f)
        list_stmt = list_stmt.where(f)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        (await session.execute(list_stmt.limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return list(rows), int(total)


async def get_record(
    session: AsyncSession, *, user_id: uuid.UUID, record_id: uuid.UUID
) -> SubscriptionRecord:
    """读单条; 不属于本人 raise NotFound."""
    stmt = select(SubscriptionRecord).where(
        SubscriptionRecord.id == record_id,
        SubscriptionRecord.user_id == user_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise SubscriptionNotFoundError(f"record {record_id} not found")
    return row


async def update_record(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    record_id: uuid.UUID,
    account_id: uuid.UUID | None = None,
    ipo_code: str | None = None,
    ipo_name: str | None = None,
    region: str | None = None,
    subscribe_shares: int | None = None,
    allotted_shares: int | None = None,
    subscribe_price: Decimal | None = None,
    margin_amount: Decimal | None = None,
    fees: Decimal | None = None,
    first_day_close: Decimal | None = None,
    sell_price: Decimal | None = None,
    sell_at: datetime | None = None,
    notes: str | None = None,
    subscribed_at: date | None = None,
    listed_at: date | None = None,
) -> SubscriptionRecord:
    """改 record. partial; PnL 自动重算后存盘.

    切换 ``account_id`` 时验新账户也属于本人.
    """
    record = await get_record(session, user_id=user_id, record_id=record_id)

    if account_id is not None and account_id != record.account_id:
        await _verify_account_owned(
            session, user_id=user_id, account_id=account_id
        )
        record.account_id = account_id

    if ipo_code is not None:
        record.ipo_code = ipo_code
    if ipo_name is not None:
        record.ipo_name = ipo_name
    if region is not None:
        record.region = region
    if subscribe_shares is not None:
        record.subscribe_shares = subscribe_shares
    if allotted_shares is not None:
        record.allotted_shares = allotted_shares
    if subscribe_price is not None:
        record.subscribe_price = subscribe_price
    if margin_amount is not None:
        record.margin_amount = margin_amount
    if fees is not None:
        record.fees = fees
    if first_day_close is not None:
        record.first_day_close = first_day_close
    if sell_price is not None:
        record.sell_price = sell_price
    if sell_at is not None:
        record.sell_at = sell_at
    if notes is not None:
        record.notes = notes
    if subscribed_at is not None:
        record.subscribed_at = subscribed_at
    if listed_at is not None:
        record.listed_at = listed_at

    # PnL 重算 (即使非 PnL 字段更新, 重算开销小, 简单一致)
    realized, unrealized = _compute_pnl(
        subscribe_price=record.subscribe_price,
        first_day_close=record.first_day_close,
        sell_price=record.sell_price,
        allotted_shares=record.allotted_shares,
        fees=record.fees,
        margin_amount=record.margin_amount,
    )
    record.realized_pnl = realized
    record.unrealized_pnl = unrealized

    await session.flush()
    await session.refresh(record)
    logger.info(
        f"sub_record.updated user={user_id} id={record_id} "
        f"unrealized_pnl={unrealized} realized_pnl={realized}"
    )
    return record


async def delete_record(
    session: AsyncSession, *, user_id: uuid.UUID, record_id: uuid.UUID
) -> None:
    """删 record. 不属于本人 raise NotFound."""
    record = await get_record(session, user_id=user_id, record_id=record_id)
    await session.delete(record)
    await session.flush()
    logger.info(f"sub_record.deleted user={user_id} id={record_id}")


# ─── 收益汇总 (BE-S6-003) ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class SummaryGroup:
    """汇总桶 (service 层中性数据结构, router 转 pydantic)."""

    key: str
    label: str
    count: int
    allotted_count: int
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None


def _format_month_label(key: str) -> str:
    """'2026-04' → '2026 年 4 月'."""
    if "-" not in key or len(key) < 7:
        return key
    y, m = key.split("-", 1)
    try:
        return f"{int(y)} 年 {int(m)} 月"
    except ValueError:
        return key


async def summarize_records(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    group_by: str,
    account_id: uuid.UUID | None = None,
    region: str | None = None,
) -> tuple[list[SummaryGroup], SummaryGroup]:
    """汇总该 user 中签记录, 返 (groups, total).

    分组维度:
    - ``group_by='month'``: 按 ``subscribed_at`` 的 YYYY-MM (PG ``to_char``)
    - ``group_by='year'``:  按 YYYY
    - ``group_by='ipo'``:   按 ``ipo_code`` (label 优先用 ipo_name, 没值 fallback ipo_code)

    汇总指标:
    - count: 本组总记录条数
    - allotted_count: 中签条数 (``allotted_shares > 0``)
    - realized_pnl: SUM(realized_pnl); NULL 跳过
    - unrealized_pnl: 同上

    ``total`` (key='_total') 是全 records 的汇总, 不受 group_by 影响.
    """
    key_expr: Any  # SQLAlchemy column or func; both expose .label() / .desc()
    if group_by == "month":
        key_expr = func.to_char(SubscriptionRecord.subscribed_at, "YYYY-MM")
    elif group_by == "year":
        key_expr = func.to_char(SubscriptionRecord.subscribed_at, "YYYY")
    elif group_by == "ipo":
        key_expr = SubscriptionRecord.ipo_code
    else:
        raise ValueError(f"unsupported group_by: {group_by}")

    count_expr = func.count(SubscriptionRecord.id).label("cnt")
    allotted_expr = func.count(
        func.nullif(SubscriptionRecord.allotted_shares, 0)
    ).label("allotted_cnt")
    realized_expr = func.sum(SubscriptionRecord.realized_pnl).label("realized_sum")
    unrealized_expr = func.sum(SubscriptionRecord.unrealized_pnl).label(
        "unrealized_sum"
    )
    name_expr = func.max(SubscriptionRecord.ipo_name).label("name_picked")

    base_filters: list[Any] = [SubscriptionRecord.user_id == user_id]
    if account_id is not None:
        base_filters.append(SubscriptionRecord.account_id == account_id)
    if region is not None:
        base_filters.append(SubscriptionRecord.region == region)

    group_stmt = select(
        key_expr.label("group_key"),
        count_expr,
        allotted_expr,
        realized_expr,
        unrealized_expr,
        name_expr,
    ).group_by(key_expr)
    for f in base_filters:
        group_stmt = group_stmt.where(f)

    if group_by == "ipo":
        order_expr = (
            func.coalesce(realized_expr, Decimal("0"))
            + func.coalesce(unrealized_expr, Decimal("0"))
        ).desc()
        group_stmt = group_stmt.order_by(order_expr)
    else:
        group_stmt = group_stmt.order_by(key_expr.desc())

    rows = (await session.execute(group_stmt)).all()
    groups: list[SummaryGroup] = []
    for r in rows:
        gk = r.group_key
        if group_by == "month":
            label = _format_month_label(gk)
        elif group_by == "year":
            label = f"{gk} 年"
        else:
            label = f"{r.name_picked} ({gk})" if r.name_picked else gk
        groups.append(
            SummaryGroup(
                key=gk,
                label=label,
                count=int(r.cnt),
                allotted_count=int(r.allotted_cnt),
                realized_pnl=r.realized_sum,
                unrealized_pnl=r.unrealized_sum,
            )
        )

    total_stmt = select(count_expr, allotted_expr, realized_expr, unrealized_expr)
    for f in base_filters:
        total_stmt = total_stmt.where(f)
    t = (await session.execute(total_stmt)).one()
    total = SummaryGroup(
        key="_total",
        label="合计",
        count=int(t.cnt),
        allotted_count=int(t.allotted_cnt),
        realized_pnl=t.realized_sum,
        unrealized_pnl=t.unrealized_sum,
    )

    return groups, total
