"""BE-S5-002 PIPL PII inventory admin 服务层.

把 ``app.services.compliance.pii_inventory`` 的静态清单 + DB 实时计数 拼装成
admin API 响应; 让 admin / 法务一站拿到全量审计信息.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuthSession, Feedback, PushToken, User
from app.services.compliance import pii_inventory


async def collect_db_counts(session: AsyncSession) -> dict[str, int]:
    """跑 5 个 count() 查询; 走索引 < 50ms 总耗时.

    所有查询都是 ``SELECT count(*)``, 无 PII 出库 — 只统计行数, 不读单条 row.
    """
    # 活跃用户: status=1 + 未软删
    active_users_q = (
        select(func.count())
        .select_from(User)
        .where(User.status == 1, User.deleted_at.is_(None))
    )

    # 历史累计 (含注销 / 禁用 / 软删) — 不加 filter
    total_users_q = select(func.count()).select_from(User)

    # 推送 token (含 inactive — 让 admin 看到清理进度)
    push_tokens_q = select(func.count()).select_from(PushToken)

    # 带 IP 的反馈 (BE-S5-004 落 INET; 没 IP 的反馈不算 PII)
    feedback_ip_q = (
        select(func.count())
        .select_from(Feedback)
        .where(Feedback.ip_inet.is_not(None))
    )

    # 活跃 refresh token (auth_sessions)
    auth_sessions_q = select(func.count()).select_from(AuthSession)

    return {
        "total_active_users": int((await session.execute(active_users_q)).scalar_one()),
        "total_users_lifetime": int((await session.execute(total_users_q)).scalar_one()),
        "total_push_tokens": int((await session.execute(push_tokens_q)).scalar_one()),
        "total_feedbacks_with_ip": int(
            (await session.execute(feedback_ip_q)).scalar_one()
        ),
        "total_auth_sessions": int(
            (await session.execute(auth_sessions_q)).scalar_one()
        ),
    }


async def build_inventory_response(session: AsyncSession) -> dict[str, object]:
    """组装 ``/admin/pii-inventory`` 响应字典.

    路由层用 ``PIIInventoryResponse.model_validate(...)`` 转 schema.
    """
    items = [item.to_dict() for item in pii_inventory.get_inventory()]
    counts = await collect_db_counts(session)

    return {
        "items": items,
        "data_export_jurisdictions": list(pii_inventory.get_jurisdictions()),
        "consent_mechanism": pii_inventory.get_consent_mechanism(),
        "third_party_sdks": list(pii_inventory.get_third_party_sdks()),
        "counts": counts,
    }


__all__ = ["build_inventory_response", "collect_db_counts"]
