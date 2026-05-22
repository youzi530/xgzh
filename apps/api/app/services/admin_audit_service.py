"""管理员操作审计服务 (Sprint 11 BE-S11-E02).

接口:
- :func:`log_admin_action`  落一行 audit log; 写失败仅打 warning, 不抛 — 业务主流程不被审计阻塞
- :func:`list_audit_logs`   分页 + filter (admin_user_id / target_type / action / 时间范围)
- :func:`diff_dict`         辅助计算 before/after diff (跳过相等的 key)

设计要点
========
1. **"审计写失败不阻塞业务"**: log_admin_action 内吃所有 exception. 审计失败时业务该返 200
   还是返 200, 但 logger.warning 上报 metric (运维监控). 真要审计 100% 强一致, 用同事务 commit.
   MVP 决策: 业务先, 审计 best-effort.
2. **不在 ORM 层自动注入审计**: SQLAlchemy event listener 太隐式. 路由层显式调用 ``log_admin_action``
   更便于 review 和测试. 复制一两行模板, 不算重复.
3. **target_id 字符串化**: 跨 UUID / 数字 / slug 通用. 调用方负责 str(uuid_or_pk).
4. **diff 用 dict 比对而非 ORM 反射**: 调用方传 before_dict 和 after_dict (从 Pydantic
   ``model_dump`` 或手工拼). 避免 ORM session 状态导致 diff 失效.
5. **只记 success — 不记 failure**: failure (e.g. 资源 not_found / 字段非法) 已经被 ``logger.warning``
   记录到日志系统. 不在 except 分支调 ``log_admin_action`` 是因为业务 session 抛异常后处于
   broken state, 此时再 spawn 新 session_factory().__aenter__ 会跟同 connection pool 的资源竞争
   导致 SQLAlchemy ``MissingGreenlet`` 错误 (greenlet context 不一致). 真要追 failure, 推荐
   FastAPI middleware 拦截 401/403/404/422 转写 audit log (Sprint 12+).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.base import get_session_factory
from app.db.models import AdminAuditLog

# 收紧 action 命名 (避免 typo / 自由发挥). 新动词在此添加.
AdminAction = Literal[
    "create",
    "update",
    "delete",
    "restore",
    "publish",
    "unpublish",
    "status_change",
    "visibility_change",
    "reset_password",
    "grant_admin",
    "revoke_admin",
]

# 资源类型. 跟 db model 表名 / FE URL 对应.
AdminTargetType = Literal[
    "broker",
    "feedback",
    "post",
    "knowledge_article",
    "user",
]


async def log_admin_action(
    *,
    admin_user_id: uuid.UUID,
    action: AdminAction,
    target_type: AdminTargetType,
    target_id: str | uuid.UUID | None = None,
    changes: dict[str, Any] | None = None,
    result: Literal["success", "failure"] = "success",
    error_message: str | None = None,
    ip_inet: str | None = None,
    user_agent: str | None = None,
) -> None:
    """落一行 audit log. 失败仅 warning, 不抛.

    使用模式 (router 层):

    .. code-block:: python

        try:
            broker = await broker_service.create_broker(...)
            await log_admin_action(
                admin_user_id=admin.user_id,
                action="create",
                target_type="broker",
                target_id=str(broker.id),
                changes={"slug": [None, broker.slug]},
                ip_inet=client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            return broker
        except Exception as e:
            await log_admin_action(
                admin_user_id=admin.user_id,
                action="create",
                target_type="broker",
                result="failure",
                error_message=str(e)[:500],
                ...
            )
            raise

    用独立 session 避免污染业务事务: 业务 session rollback 不应该清掉审计.
    """
    session_factory: async_sessionmaker[AsyncSession] = get_session_factory()
    target_id_str = str(target_id) if target_id is not None else None
    try:
        async with session_factory() as session:
            log_row = AdminAuditLog(
                admin_user_id=admin_user_id,
                action=action,
                target_type=target_type,
                target_id=target_id_str,
                changes_json=changes,
                result=result,
                error_message=(error_message or "")[:2000] if error_message else None,
                ip_inet=ip_inet,
                user_agent=(user_agent or "")[:1000] if user_agent else None,
            )
            session.add(log_row)
            await session.commit()
        logger.info(
            f"admin.audit admin={admin_user_id} action={action} "
            f"target={target_type}:{target_id_str} result={result}"
        )
    except Exception as e:  # noqa: BLE001
        # 审计失败不阻塞业务. 监控 admin_audit_failure metric.
        logger.warning(
            f"admin.audit.write_failed admin={admin_user_id} action={action} "
            f"target={target_type}:{target_id_str} err={e}"
        )


def diff_dict(
    before: dict[str, Any], after: dict[str, Any], keys: list[str] | None = None
) -> dict[str, list[Any]]:
    """计算 before/after 字典 diff. 只返不同的 key.

    Returns:
        dict[k, [before_val, after_val]] for k in (keys ∪ all_keys) if before != after

    Examples:
        >>> diff_dict({"a": 1, "b": 2}, {"a": 1, "b": 3})
        {"b": [2, 3]}
    """
    if keys is None:
        keys = list(set(before.keys()) | set(after.keys()))
    diff: dict[str, list[Any]] = {}
    for k in keys:
        b = before.get(k)
        a = after.get(k)
        if b != a:
            diff[k] = [b, a]
    return diff


async def list_audit_logs(
    session: AsyncSession,
    *,
    admin_user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AdminAuditLog], int]:
    """读 audit logs (admin 自查 / 安全审计).

    倒序 created_at. 用 ``(admin_user_id, created_at DESC)`` 索引或
    ``(target_type, target_id, created_at DESC)`` 复合索引, 都跑 index scan.
    """
    from sqlalchemy import desc, func
    from sqlalchemy.sql import ColumnElement

    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 1
    if page_size > 200:
        page_size = 200
    offset = (page - 1) * page_size

    filters: list[ColumnElement[bool]] = []
    if admin_user_id is not None:
        filters.append(AdminAuditLog.admin_user_id == admin_user_id)
    if target_type is not None:
        filters.append(AdminAuditLog.target_type == target_type)
    if target_id is not None:
        filters.append(AdminAuditLog.target_id == target_id)
    if action is not None:
        filters.append(AdminAuditLog.action == action)
    if since is not None:
        filters.append(AdminAuditLog.created_at >= since)
    if until is not None:
        filters.append(AdminAuditLog.created_at < until)

    count_stmt = select(func.count(AdminAuditLog.id))
    list_stmt = select(AdminAuditLog).order_by(desc(AdminAuditLog.created_at))
    for f in filters:
        count_stmt = count_stmt.where(f)
        list_stmt = list_stmt.where(f)

    total = int((await session.execute(count_stmt)).scalar() or 0)
    rows = (
        (await session.execute(list_stmt.limit(page_size).offset(offset)))
        .scalars()
        .all()
    )
    return list(rows), total


def resolve_request_context(request: Any) -> tuple[str | None, str | None]:
    """从 FastAPI ``Request`` 提取 ip 和 user-agent.

    支持 ``X-Forwarded-For`` (走反代时取链上第一个 ip; 直连退回 ``request.client.host``).
    返回 (ip, ua); 都可能 None.
    """
    if request is None:
        return None, None
    try:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            ip = xff.split(",")[0].strip() or None
        elif request.client is not None:
            ip = request.client.host
        else:
            ip = None
        ua = request.headers.get("user-agent")
        return ip, ua
    except Exception:  # noqa: BLE001
        return None, None


__all__ = [
    "AdminAction",
    "AdminTargetType",
    "diff_dict",
    "list_audit_logs",
    "log_admin_action",
    "resolve_request_context",
]
