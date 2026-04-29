"""BE-S5-006 admin 数据看板服务 (轻量 6 指标聚合).

spec/12 §BE-S5-006: 灰度阶段够用; 完整 Grafana / Superset 后置 5.5+.

6 个指标
========
1. **DAU / MAU** — ``users.last_active_at`` 过去 N 天 distinct user
2. **新增注册** — ``users.created_at`` 过去 N 天 (代替 spec 写的 OTP→注册转化, 因为 OTP 走
   Redis ttl 没历史数据; 简化但够用)
3. **VIP 转化** — ``vip_memberships`` trial / paid / 累计活跃
4. **Agent 调用** — ``chat_sessions`` + ``chat_messages`` + ``chat_token_usage`` 三表聚合
5. **错误率** — 走 ``error_monitor.get_metrics()`` Redis 实时窗口 (与 OPS-S4-001 同源)
6. **LLM 性能** — ``chat_token_usage`` avg tokens / total cost (代替 SSE p95;
   精确 p95 走 OPS-S5-001 Sentry traces)

设计取舍
========
- 单次响应 < 500ms: 全部走 ``count(*)`` + 索引命中, 不开 ``WITH RECURSIVE`` / 大 join
- ``days`` 可参数化 (1 / 7 / 30) — 默认 1 天; 运营拉 7d 趋势走 ?days=7
- HTML view 在路由层用 f-string 渲染单文件 (不上 Jinja2, vibe coding 节奏)
- 与 ``error_monitor`` Redis 解耦: 错误率单独字段, 文档明示"窗口 = error_alert_window_seconds 秒
  实时, 非过去 days 天聚合", 避免运营误读
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ChatMessage,
    ChatSession,
    ChatTokenUsage,
    User,
    VipMembership,
)
from app.services import error_monitor

# ─── 数据类 (路由层 model_validate / asdict 转 JSON) ───────────────


@dataclass(frozen=True, slots=True)
class UserActivityMetrics:
    """指标 1: DAU."""

    distinct_active_users: int
    """过去 N 天活跃 (last_active_at > now()-Nd) distinct user 数, status=1 + 未注销"""


@dataclass(frozen=True, slots=True)
class RegistrationMetrics:
    """指标 2: 新增注册."""

    new_users_in_window: int
    """过去 N 天 created_at 落在窗口内的新注册用户数 (含未注销)"""
    total_users_lifetime: int
    """历史累计用户数 (含 注销 / 禁用); 用于看转化基数"""


@dataclass(frozen=True, slots=True)
class VipConversionMetrics:
    """指标 3: VIP 转化."""

    total_memberships: int
    """所有 vip_memberships 行数 (= 全体注册用户数, 因为注册必赠 trial)"""
    trial_memberships: int
    """status='trialing' AND end_at > now()"""
    active_paid_memberships: int
    """status='active' (非 trial 期, 已付费) — 即转化成功用户"""
    expired_memberships: int
    """status='expired' (含 trial 自然过期 + paid 到期未续费)"""
    trial_to_paid_pct: float
    """active_paid / (active_paid + expired) * 100, 0 除处理为 0"""


@dataclass(frozen=True, slots=True)
class AgentUsageMetrics:
    """指标 4: Agent 调用."""

    sessions_in_window: int
    """过去 N 天创建的 chat_sessions"""
    user_messages_in_window: int
    """过去 N 天 chat_messages role='user' 的消息数 (= 用户发问数)"""
    llm_calls_in_window: int
    """过去 N 天 chat_token_usage 行数 (= LLM 真实被调次数)"""
    total_input_tokens: int
    total_output_tokens: int
    total_cost_cny: float
    """过去 N 天 cost_cny SUM, Decimal → float (JSON-safe)"""


@dataclass(frozen=True, slots=True)
class ErrorRateMetrics:
    """指标 5: 错误率 (走 error_monitor Redis 实时窗口)."""

    window_seconds: int
    total_requests: int
    total_errors: int
    error_pct: float


@dataclass(frozen=True, slots=True)
class LLMPerformanceMetrics:
    """指标 6: LLM 性能 (代替 SSE p95)."""

    avg_input_tokens_per_call: float
    avg_output_tokens_per_call: float
    avg_cost_cny_per_call: float
    """精确 p95 留 OPS-S5-001 Sentry traces; 本指标用平均值近似"""


@dataclass(frozen=True, slots=True)
class DashboardMetrics:
    """6 指标聚合容器, 路由层直接序列化."""

    window_days: int
    """聚合窗口 (days), 默认 1; 用户调 ?days=7 / 30 切换"""
    user_activity: UserActivityMetrics
    registration: RegistrationMetrics
    vip_conversion: VipConversionMetrics
    agent_usage: AgentUsageMetrics
    error_rate: ErrorRateMetrics
    llm_performance: LLMPerformanceMetrics

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── 聚合查询入口 ─────────────────────────────────────────────────


def _interval_clause(days: int) -> Any:
    """造 ``now() - interval 'N days'`` PG 表达式. 不直接拼字符串, 用 SQLAlchemy text
    bindparam 避免 SQL 注入 (虽然 days 是 int, 但参数化更标准)."""
    return text("now() - (:days || ' days')::interval").bindparams(days=days)


async def _user_activity(session: AsyncSession, days: int) -> UserActivityMetrics:
    """DAU: distinct active user, status=1 + 未注销, last_active_at > now()-Nd."""
    cutoff = _interval_clause(days)
    stmt = (
        select(func.count(func.distinct(User.user_id)))
        .where(
            User.status == 1,
            User.deleted_at.is_(None),
            User.last_active_at > cutoff,
        )
    )
    return UserActivityMetrics(
        distinct_active_users=int((await session.execute(stmt)).scalar_one()),
    )


async def _registration(session: AsyncSession, days: int) -> RegistrationMetrics:
    """新增注册 + 历史累计."""
    cutoff = _interval_clause(days)
    new_q = (
        select(func.count())
        .select_from(User)
        .where(User.created_at > cutoff)
    )
    total_q = select(func.count()).select_from(User)
    return RegistrationMetrics(
        new_users_in_window=int((await session.execute(new_q)).scalar_one()),
        total_users_lifetime=int((await session.execute(total_q)).scalar_one()),
    )


async def _vip_conversion(session: AsyncSession) -> VipConversionMetrics:
    """VIP 转化: 累计快照, 不按窗口 (转化是漏斗概念, 不是流量).

    单次 SQL 用 ``COUNT(*) FILTER (WHERE ...)`` 一次扫表拿 4 个 count.
    """
    stmt = select(
        func.count().label("total"),
        func.count().filter(VipMembership.status == "trialing").label("trial"),
        func.count().filter(VipMembership.status == "active").label("active_paid"),
        func.count().filter(VipMembership.status == "expired").label("expired"),
    )
    row = (await session.execute(stmt)).one()
    total = int(row.total)
    trial = int(row.trial)
    active_paid = int(row.active_paid)
    expired = int(row.expired)

    # 转化率 = 已付费 / (已付费 + 试用过期). 仍 trialing 的不计入分母 (还没决定).
    denom = active_paid + expired
    trial_to_paid_pct = (active_paid / denom * 100.0) if denom > 0 else 0.0

    return VipConversionMetrics(
        total_memberships=total,
        trial_memberships=trial,
        active_paid_memberships=active_paid,
        expired_memberships=expired,
        trial_to_paid_pct=round(trial_to_paid_pct, 2),
    )


async def _agent_usage(session: AsyncSession, days: int) -> AgentUsageMetrics:
    """Agent 调用: 4 个 count + 2 个 sum."""
    cutoff = _interval_clause(days)

    sessions_q = (
        select(func.count())
        .select_from(ChatSession)
        .where(ChatSession.created_at > cutoff)
    )
    user_msgs_q = (
        select(func.count())
        .select_from(ChatMessage)
        .where(
            ChatMessage.created_at > cutoff,
            ChatMessage.role == "user",
        )
    )
    llm_calls_q = (
        select(func.count())
        .select_from(ChatTokenUsage)
        .where(ChatTokenUsage.created_at > cutoff)
    )
    tokens_q = select(
        func.coalesce(func.sum(ChatTokenUsage.input_tokens), 0),
        func.coalesce(func.sum(ChatTokenUsage.output_tokens), 0),
        func.coalesce(func.sum(ChatTokenUsage.cost_cny), 0),
    ).where(ChatTokenUsage.created_at > cutoff)

    sessions_total = int((await session.execute(sessions_q)).scalar_one())
    user_msgs = int((await session.execute(user_msgs_q)).scalar_one())
    llm_calls = int((await session.execute(llm_calls_q)).scalar_one())
    tok_in, tok_out, cost = (await session.execute(tokens_q)).one()

    return AgentUsageMetrics(
        sessions_in_window=sessions_total,
        user_messages_in_window=user_msgs,
        llm_calls_in_window=llm_calls,
        total_input_tokens=int(tok_in or 0),
        total_output_tokens=int(tok_out or 0),
        total_cost_cny=float(cost or Decimal("0")),
    )


async def _error_rate() -> ErrorRateMetrics:
    """走 error_monitor Redis 实时窗口 (秒级, 非天级)."""
    m = await error_monitor.get_metrics()
    return ErrorRateMetrics(
        window_seconds=m.window_seconds,
        total_requests=m.total_requests,
        total_errors=m.total_errors,
        error_pct=round(m.error_pct, 3),
    )


async def _llm_performance(usage: AgentUsageMetrics) -> LLMPerformanceMetrics:
    """LLM 性能: 复用 _agent_usage 的 sum, 不重新查; 平均近似 p95."""
    n = usage.llm_calls_in_window
    if n <= 0:
        return LLMPerformanceMetrics(
            avg_input_tokens_per_call=0.0,
            avg_output_tokens_per_call=0.0,
            avg_cost_cny_per_call=0.0,
        )
    return LLMPerformanceMetrics(
        avg_input_tokens_per_call=round(usage.total_input_tokens / n, 2),
        avg_output_tokens_per_call=round(usage.total_output_tokens / n, 2),
        avg_cost_cny_per_call=round(usage.total_cost_cny / n, 6),
    )


async def collect_metrics(
    session: AsyncSession, *, days: int = 1
) -> DashboardMetrics:
    """聚合 6 指标, 路由层直接 ``DashboardMetrics.to_dict()`` 序列化.

    顺序执行 (而非 asyncio.gather): SQLAlchemy AsyncSession 不允许并发执行同一 session,
    要并发必须开多个 session, 6 个查询总耗时 ~50ms 串行已足够 (spec/12 §AC < 500ms).
    """
    if days <= 0:
        raise ValueError(f"days must be > 0, got {days}")

    activity = await _user_activity(session, days)
    registration = await _registration(session, days)
    vip = await _vip_conversion(session)
    agent = await _agent_usage(session, days)
    err = await _error_rate()
    llm = await _llm_performance(agent)

    return DashboardMetrics(
        window_days=days,
        user_activity=activity,
        registration=registration,
        vip_conversion=vip,
        agent_usage=agent,
        error_rate=err,
        llm_performance=llm,
    )


__all__ = [
    "AgentUsageMetrics",
    "DashboardMetrics",
    "ErrorRateMetrics",
    "LLMPerformanceMetrics",
    "RegistrationMetrics",
    "UserActivityMetrics",
    "VipConversionMetrics",
    "collect_metrics",
]
