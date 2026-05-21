"""Admin 用户管理 service (Sprint 10 BE-S10-004 / BE-S10-005).

负责: 列表搜索 / 详情聚合 / 字段更新 / 软删 / 加 VIP 时长 5 个用例.

与 ``user_service`` 的关键差异:
- ``user_service`` 所有查询都自动 ``WHERE deleted_at IS NULL`` (注销用户视为不存在),
  本 service 显式接受 ``include_deleted`` 参数 — admin 排查需要能看软删后的行
- ``user_service`` 只对单 user 操作, 本 service 还有聚合查询 (vip + invite count)
- 写操作都打 ``admin.action`` info log; Sprint 11 加 ``admin_audit_logs`` 表时把这些
  log 改成 audit log entry (不动调用侧 service 函数签名)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger
from sqlalchemy import and_, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.models.invite import InviteCode
from app.db.models.vip import VipMembership
from app.security.blacklist import blacklist_jti  # noqa: F401  # 暂未使用; admin 软删不拉黑 access (admin 不持有目标 access jti)
from app.services import vip_service
from app.utils.email import mask_email
from app.utils.phone import mask_phone


class UserNotFoundError(Exception):
    """目标 user_id 不存在 (admin 查/改/删时路径参数 user_id 已 404)."""


class CannotDeleteSelfError(Exception):
    """admin 试图软删自己 — 防止误操作锁死管理员账号."""


class CannotDemoteSelfError(Exception):
    """admin 试图把自己改成 status != 1 — 同上, 防止自锁."""


@dataclass(frozen=True, slots=True)
class _AggRow:
    """聚合查询返回一行: user + vip_status/end_at + invite_count."""

    user: User
    vip_status: str | None
    vip_plan: str | None
    vip_start_at: datetime | None
    vip_end_at: datetime | None
    vip_total_paid_cny: Decimal | None
    invite_count: int


# ─── 查询 ──────────────────────────────────────────────────────────


async def list_users_with_aggregate(
    session: AsyncSession,
    *,
    q: str | None = None,
    is_admin: bool | None = None,
    include_deleted: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[_AggRow], int]:
    """列表 + 搜索 + 聚合 vip + invite count.

    搜索匹配 (大小写不敏感, OR):
    - ``q``: ilike ``%q%`` 命中 phone OR email OR nickname
    - phone 不 normalize — 用户搜 ``130074`` 应该能命中 ``+8613007458553``;
      ilike + 通配 ``%`` 足够 (DB 存的是 normalized phone, 不会有空格/破折号歧义)

    过滤:
    - ``is_admin=True``: 仅返管理员; ``is_admin=False``: 仅返非管理员; ``None``: 全返
    - ``include_deleted=False`` (默认): 仅 deleted_at IS NULL; ``True``: 含已软删

    返回:
    - ``items``: 当前页的 _AggRow (含 user + vip + invite_count)
    - ``total``: 符合筛选条件的总行数 (用于 FE 算 total_pages)

    分页用 offset/limit 而非 cursor — admin 用 case 量小 (期望全表 < 1w),
    offset 性能可接受; cursor 等 Sprint 12 通用列表性能优化时再加.
    """
    base_filters: list = []
    if q:
        pattern = f"%{q.strip()}%"
        base_filters.append(
            or_(
                User.phone.ilike(pattern),
                User.email.ilike(pattern),
                User.nickname.ilike(pattern),
            )
        )
    if is_admin is not None:
        base_filters.append(User.is_admin.is_(is_admin))
    if not include_deleted:
        base_filters.append(User.deleted_at.is_(None))

    # ── total 走 COUNT(*) 子查询 — 不用 OVER() 是因为 LIMIT/OFFSET 时
    #    window 函数会重复算 (PG 实测每行都全表扫 invite_count 计算); 拆两次执行更稳。
    #    用 and_(true(), *filters) 兼容空 filter 列表 (SQLAlchemy 2.x and_() 空参数 deprecate).
    where_clause = and_(true(), *base_filters)
    count_stmt = select(func.count()).select_from(User).where(where_clause)
    total = (await session.execute(count_stmt)).scalar_one()

    # ── 主查询: User LEFT JOIN VipMembership + 子查询统计 invite_count
    invite_count_subq = (
        select(
            User.invited_by.label("inviter"),
            func.count().label("cnt"),
        )
        .where(User.deleted_at.is_(None))  # 邀请数不含被注销的人
        .where(User.invited_by.is_not(None))
        .group_by(User.invited_by)
        .subquery()
    )

    stmt = (
        select(
            User,
            VipMembership.status,
            VipMembership.plan,
            VipMembership.start_at,
            VipMembership.end_at,
            VipMembership.total_paid_cny,
            func.coalesce(invite_count_subq.c.cnt, 0).label("invite_count"),
        )
        .select_from(User)
        .outerjoin(VipMembership, VipMembership.user_id == User.user_id)
        .outerjoin(
            invite_count_subq,
            invite_count_subq.c.inviter == User.user_id,
        )
        .where(where_clause)
        .order_by(User.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )

    rows = (await session.execute(stmt)).all()
    items = [
        _AggRow(
            user=row[0],
            vip_status=row[1],
            vip_plan=row[2],
            vip_start_at=row[3],
            vip_end_at=row[4],
            vip_total_paid_cny=row[5],
            invite_count=int(row[6]),
        )
        for row in rows
    ]
    return items, total


async def get_user_aggregate(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    include_deleted: bool = True,
) -> _AggRow:
    """单用户详情 + 聚合. 不存在抛 ``UserNotFoundError``.

    与 list 接口默认相反: 详情接口默认 ``include_deleted=True``, admin 点进详情
    多半就是为了查这个软删用户怎么回事, 不应被 deleted_at IS NULL 挡掉。
    """
    invite_count_subq = (
        select(func.count())
        .select_from(User)
        .where(User.invited_by == user_id, User.deleted_at.is_(None))
        .scalar_subquery()
    )

    stmt = (
        select(
            User,
            VipMembership.status,
            VipMembership.plan,
            VipMembership.start_at,
            VipMembership.end_at,
            VipMembership.total_paid_cny,
            invite_count_subq.label("invite_count"),
        )
        .select_from(User)
        .outerjoin(VipMembership, VipMembership.user_id == User.user_id)
        .where(User.user_id == user_id)
    )
    if not include_deleted:
        stmt = stmt.where(User.deleted_at.is_(None))

    row = (await session.execute(stmt)).first()
    if row is None:
        raise UserNotFoundError(f"user {user_id} not found")

    return _AggRow(
        user=row[0],
        vip_status=row[1],
        vip_plan=row[2],
        vip_start_at=row[3],
        vip_end_at=row[4],
        vip_total_paid_cny=row[5],
        invite_count=int(row[6]),
    )


# ─── 写操作 ────────────────────────────────────────────────────────


async def patch_user(
    session: AsyncSession,
    *,
    admin: User,
    target_user_id: uuid.UUID,
    nickname: str | None = None,
    region: str | None = None,
    status_val: int | None = None,
) -> _AggRow:
    """PATCH /admin/users/{id}.

    安全保护:
    - admin 不能改自己的 ``status`` (防自锁; 改 nickname/region 可以)
    - status 仅接 -1/0/1 (Pydantic Literal 已挡, defense-in-depth 再判一次)
    - 其它字段 (phone / email / is_admin) 拒绝出现在 patch 字典里 (schema 已挡)
    - 软删用户也允许编辑 (admin 可能在恢复中); 但禁用/封禁本来就是软删后状态可接受
    """
    target = await session.get(User, target_user_id)
    if target is None or target.deleted_at is None and False:  # noqa: SIM108 — keep readable
        pass  # 不可达; 兜底 None check 在下面
    if target is None:
        raise UserNotFoundError(f"user {target_user_id} not found")

    if status_val is not None and target.user_id == admin.user_id:
        raise CannotDemoteSelfError(
            f"admin {admin.user_id} cannot change own status"
        )

    changed_fields: list[str] = []
    if nickname is not None and nickname != target.nickname:
        target.nickname = nickname
        changed_fields.append("nickname")
    if region is not None and region != target.region:
        target.region = region
        changed_fields.append("region")
    if status_val is not None and status_val != target.status:
        target.status = status_val
        changed_fields.append("status")

    if changed_fields:
        await session.flush()
        await session.commit()
        logger.info(
            f"admin.user.patch admin_id={admin.user_id} target_id={target.user_id} "
            f"fields={changed_fields}"
        )

    return await get_user_aggregate(session, target_user_id)


async def soft_delete_user_by_admin(
    session: AsyncSession,
    *,
    admin: User,
    target_user_id: uuid.UUID,
) -> None:
    """DELETE /admin/users/{id} — admin 版本的软删, 简化版.

    与 ``user_deletion_service.soft_delete_user`` (用户自己 DELETE /me) 区别:
    - 不写 ``user_deletions`` 表 (那个表 unique 约束在 user_id, 防止用户重复申请;
      admin 操作不走那条路径) — 后续 Sprint 11 加 ``admin_audit_logs`` 替代
    - 不拉黑 access jti (admin 拿不到目标用户的 access payload)
    - 仍然走 invite_codes 标 is_active=False (注销用户的码不应能被新用户绑)
    - 拉黑该用户所有 active refresh sessions — 强制下线

    审计 trail: logger.info admin.user.soft_delete (Sprint 11 进 admin_audit_logs).

    Raises:
        UserNotFoundError: target_user_id 不存在
        CannotDeleteSelfError: admin 试图删自己 (防自锁)
    """
    if target_user_id == admin.user_id:
        raise CannotDeleteSelfError(
            f"admin {admin.user_id} cannot soft-delete self"
        )

    target = await session.get(User, target_user_id)
    if target is None:
        raise UserNotFoundError(f"user {target_user_id} not found")

    if target.deleted_at is not None:
        # 幂等: 已软删的再删一次, 不报错也不重写 deleted_at
        logger.info(
            f"admin.user.soft_delete.noop admin_id={admin.user_id} "
            f"target_id={target_user_id} (already deleted at {target.deleted_at})"
        )
        return

    now = datetime.now(UTC)

    # 1. 标软删 + 禁用
    await session.execute(
        update(User)
        .where(User.user_id == target_user_id)
        .values(deleted_at=now, status=0)
    )

    # 2. 把该用户的邀请码全标 inactive (防止已发出的码继续被绑)
    await session.execute(
        update(InviteCode)
        .where(
            InviteCode.owner_user_id == target_user_id,
            InviteCode.is_active.is_(True),
        )
        .values(is_active=False)
    )

    # 3. 拉黑目标用户的所有 active refresh sessions — 强制下线
    #    (用户主动 DELETE /me 时还会再加一步"拉黑当前 access jti", admin 操作
    #    没拿到目标 access, 不做这步; 目标 access 自然在 30min 内过期, status=0
    #    也让 get_current_user 401 user_disabled, 双保险)
    from app.db.models.auth import AuthSession  # noqa: PLC0415 — 避免顶层循环 import

    await session.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == target_user_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )

    await session.commit()

    logger.info(
        f"admin.user.soft_delete admin_id={admin.user_id} "
        f"target_id={target_user_id} "
        f"phone={mask_phone(target.phone) if target.phone else 'none'} "
        f"email={mask_email(target.email) if target.email else 'none'}"
    )


async def grant_vip_to_user(
    session: AsyncSession,
    *,
    admin: User,
    target_user_id: uuid.UUID,
    days: int,
    reason: str,
) -> _AggRow:
    """POST /admin/users/{id}/grant-vip — 加 VIP 时长.

    复用 ``vip_service.extend_membership`` (与 invite reward 同款), 不重复写状态机.
    幂等性: **非幂等** (拍板) — 连续点 2 次 = 2N 天; FE 二次确认对此提示.

    Raises:
        UserNotFoundError: target 不存在或已软删 (admin 不应给软删用户加 VIP)
    """
    target = await session.get(User, target_user_id)
    if target is None or target.deleted_at is not None:
        raise UserNotFoundError(
            f"user {target_user_id} not found or already deleted"
        )

    snapshot = await vip_service.extend_membership(
        session,
        user_id=target_user_id,
        days=days,
        reason=f"admin_grant:admin_id={admin.user_id}:reason={reason[:80]}",
    )
    await session.commit()

    logger.info(
        f"admin.user.grant_vip admin_id={admin.user_id} "
        f"target_id={target_user_id} +{days}d reason={reason[:50]!r} "
        f"new_end_at={snapshot.end_at.isoformat()}"
    )

    return await get_user_aggregate(session, target_user_id)


__all__ = [
    "CannotDeleteSelfError",
    "CannotDemoteSelfError",
    "UserNotFoundError",
    "_AggRow",
    "get_user_aggregate",
    "grant_vip_to_user",
    "list_users_with_aggregate",
    "patch_user",
    "soft_delete_user_by_admin",
]
