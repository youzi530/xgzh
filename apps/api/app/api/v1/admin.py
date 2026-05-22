"""OPS-S4-001 + BE-S5-002/004/006 Admin API: 灰度旋钮 + 错误率 + 反馈 + PIPL + 数据看板.

路由总览 (全部走 ``X-Admin-Token`` header 鉴权, 见 ``security/admin.py``):

| Method | Path                          | 用途                                |
|--------|-------------------------------|-------------------------------------|
| GET    | /api/v1/admin/flags           | 列所有 flag 配置                    |
| GET    | /api/v1/admin/flags/{name}    | 查单 flag                           |
| PUT    | /api/v1/admin/flags/{name}    | 写 / 改 flag (admin-write)          |
| DELETE | /api/v1/admin/flags/{name}    | 删 flag                             |
| GET    | /api/v1/admin/metrics         | 当前窗口 错误率 / total / errors    |
| POST   | /api/v1/admin/metrics/reset   | 清当前窗口计数 (debug / 灰度回滚后) |
| GET    | /api/v1/admin/ops/feedbacks   | 反馈列表 (Sprint 11 从 /admin/feedbacks 迁移; ops X-Admin-Token) |
| GET    | /api/v1/admin/pii-inventory   | PIPL 个人信息收集清单 + 实时计数    |
| GET    | /api/v1/admin/dashboard       | 6 指标数据看板 (?days&format)       |

注意:
- 所有路由都 ``require_admin_token`` Depends, ``OPS_ADMIN_TOKEN`` 留空时返 503
- ``flags`` 与 ``metrics`` 的语义都是"运维触达, 不在用户产品里出现"; 不在 OpenAPI
  schema 上设 ``include_in_schema=False`` 是为了让 ops 的同事能从 ``/docs`` 直接试,
  生产实际靠"未配 OPS_ADMIN_TOKEN → 503"做最后保险。
"""

from __future__ import annotations

from typing import Self

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import HTMLResponse, Response
from loguru import logger
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.schemas.admin_dashboard import DashboardResponse
from app.schemas.auth import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    _validate_password_format,
)
from app.schemas.feedback import (
    FeedbackAdminItem,
    FeedbackAdminListResponse,
    FeedbackCategory,
    FeedbackPlatform,
)
from app.schemas.pii_inventory import PIIInventoryResponse
from app.security.admin import require_admin_token
from app.services import (
    admin_dashboard_service,
    error_monitor,
    feature_flags,
    feedback_service,
    pii_inventory_service,
)
from app.services.security_password import hash_password
from app.utils.phone import mask_phone, normalize_phone

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


# ─── Feedbacks 路由 (BE-S5-004 / Sprint 11 迁移到 ops/feedbacks) ────────────
#
# Sprint 11 BE-S11-B02 引入了 JWT 鉴权的 ``/admin/feedbacks`` (admin_feedbacks.py).
# 为了避免 URL 冲突, 老 X-Admin-Token 路径迁移到 ``/admin/ops/feedbacks``:
# - 老 ops 脚本 (如果有) 需要把 URL 从 ``/admin/feedbacks`` → ``/admin/ops/feedbacks``
# - 新 in-app admin (走 JWT + is_admin) 直接用 ``/admin/feedbacks``
# - 两路径鉴权完全独立, 不冲突
#
# 迁移决策: Sprint 11 拍板 Q4=A 双系统并存, 但物理 URL 不能同时 own; 选 ops 移让 REST
# 标准路径给 in-app admin, ops 脚本要 update, 但 ops 路径很少有人调 (没有 cron / dashboard 调用).


@router.get(
    "/ops/feedbacks",
    response_model=FeedbackAdminListResponse,
    dependencies=[Depends(require_admin_token)],
    summary="拉反馈列表 (ops X-Admin-Token; Sprint 11 从 /admin/feedbacks 迁移过来)",
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


# ─── Dashboard 路由 (BE-S5-006) ────────────────────────────────────


_DASHBOARD_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>XGZH Admin Dashboard ({days}d)</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif;max-width:960px;margin:32px auto;padding:0 16px;color:#222;}}
h1{{font-size:22px;margin:0 0 8px;}}
.meta{{color:#666;font-size:13px;margin-bottom:24px;}}
.meta a{{margin-right:12px;}}
section{{margin-bottom:28px;}}
section h2{{font-size:16px;border-bottom:1px solid #eee;padding-bottom:6px;margin:0 0 12px;}}
table{{border-collapse:collapse;width:100%;font-size:14px;}}
th,td{{border:1px solid #eee;padding:6px 10px;text-align:left;vertical-align:top;}}
th{{background:#fafafa;width:240px;color:#555;font-weight:500;}}
td.num{{font-family:"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums;}}
.notice{{background:#fff8e1;border-left:3px solid #f5a623;padding:8px 12px;margin-bottom:18px;font-size:12px;color:#7a5a00;}}
</style>
</head>
<body>
<h1>XGZH Admin Dashboard</h1>
<p class="meta">窗口 = 过去 <b>{days}</b> 天 ·
<a href="?days=1&format=html">1d</a>
<a href="?days=7&format=html">7d</a>
<a href="?days=30&format=html">30d</a>
· <a href="?days={days}&format=json">JSON</a>
· <a href="?days={days}&format=html">刷新</a></p>

<div class="notice">
错误率走 <code>error_monitor</code> Redis 实时滑窗 ({error_window_seconds}s),
非 days 天聚合; 精确 SSE p95 走 OPS-S5-001 Sentry traces.
</div>

<section>
<h2>1. 用户活跃 (DAU)</h2>
<table>
<tr><th>过去 {days} 天 distinct 活跃用户</th><td class="num">{distinct_active_users}</td></tr>
</table>
</section>

<section>
<h2>2. 注册</h2>
<table>
<tr><th>过去 {days} 天新增注册</th><td class="num">{new_users_in_window}</td></tr>
<tr><th>历史累计用户数</th><td class="num">{total_users_lifetime}</td></tr>
</table>
</section>

<section>
<h2>3. VIP 转化</h2>
<table>
<tr><th>VIP 会员总行数</th><td class="num">{total_memberships}</td></tr>
<tr><th>试用中 (trialing)</th><td class="num">{trial_memberships}</td></tr>
<tr><th>活跃付费 (active)</th><td class="num">{active_paid_memberships}</td></tr>
<tr><th>已过期 (expired)</th><td class="num">{expired_memberships}</td></tr>
<tr><th>试用→付费转化率</th><td class="num">{trial_to_paid_pct}%</td></tr>
</table>
</section>

<section>
<h2>4. Agent 调用 (过去 {days} 天)</h2>
<table>
<tr><th>会话数</th><td class="num">{sessions_in_window}</td></tr>
<tr><th>用户消息数</th><td class="num">{user_messages_in_window}</td></tr>
<tr><th>LLM 调用数</th><td class="num">{llm_calls_in_window}</td></tr>
<tr><th>输入 tokens</th><td class="num">{total_input_tokens}</td></tr>
<tr><th>输出 tokens</th><td class="num">{total_output_tokens}</td></tr>
<tr><th>总成本 CNY</th><td class="num">¥ {total_cost_cny}</td></tr>
</table>
</section>

<section>
<h2>5. 错误率 (Redis 实时窗口)</h2>
<table>
<tr><th>窗口 (秒)</th><td class="num">{error_window_seconds}</td></tr>
<tr><th>请求总数</th><td class="num">{total_requests}</td></tr>
<tr><th>5xx / unhandled</th><td class="num">{total_errors}</td></tr>
<tr><th>错误率</th><td class="num">{error_pct}%</td></tr>
</table>
</section>

<section>
<h2>6. LLM 性能 (平均近似 p95)</h2>
<table>
<tr><th>平均输入 tokens / 次</th><td class="num">{avg_input_tokens_per_call}</td></tr>
<tr><th>平均输出 tokens / 次</th><td class="num">{avg_output_tokens_per_call}</td></tr>
<tr><th>平均成本 / 次 (CNY)</th><td class="num">¥ {avg_cost_cny_per_call}</td></tr>
</table>
</section>

</body>
</html>
"""


def _render_dashboard_html(payload: DashboardResponse) -> str:
    """把 DashboardResponse 渲染成单页 HTML.

    用 ``str.format`` 而非 Jinja2: spec/12 §BE-S5-006 明示"不上 Vue/React, 能用就行";
    模板里所有 ``{{`` / ``}}`` 是真大括号 (CSS), ``{xxx}`` 是占位符.
    """
    return _DASHBOARD_HTML_TEMPLATE.format(
        days=payload.window_days,
        distinct_active_users=payload.user_activity.distinct_active_users,
        new_users_in_window=payload.registration.new_users_in_window,
        total_users_lifetime=payload.registration.total_users_lifetime,
        total_memberships=payload.vip_conversion.total_memberships,
        trial_memberships=payload.vip_conversion.trial_memberships,
        active_paid_memberships=payload.vip_conversion.active_paid_memberships,
        expired_memberships=payload.vip_conversion.expired_memberships,
        trial_to_paid_pct=payload.vip_conversion.trial_to_paid_pct,
        sessions_in_window=payload.agent_usage.sessions_in_window,
        user_messages_in_window=payload.agent_usage.user_messages_in_window,
        llm_calls_in_window=payload.agent_usage.llm_calls_in_window,
        total_input_tokens=payload.agent_usage.total_input_tokens,
        total_output_tokens=payload.agent_usage.total_output_tokens,
        total_cost_cny=f"{payload.agent_usage.total_cost_cny:.4f}",
        error_window_seconds=payload.error_rate.window_seconds,
        total_requests=payload.error_rate.total_requests,
        total_errors=payload.error_rate.total_errors,
        error_pct=payload.error_rate.error_pct,
        avg_input_tokens_per_call=payload.llm_performance.avg_input_tokens_per_call,
        avg_output_tokens_per_call=payload.llm_performance.avg_output_tokens_per_call,
        avg_cost_cny_per_call=f"{payload.llm_performance.avg_cost_cny_per_call:.6f}",
    )


@router.get(
    "/dashboard",
    dependencies=[Depends(require_admin_token)],
    summary="6 指标数据看板 (DAU / 注册 / VIP / Agent / 错误率 / LLM 性能)",
    responses={
        200: {
            "description": "JSON or HTML 双格式",
            "content": {
                "application/json": {"schema": DashboardResponse.model_json_schema()},
                "text/html": {"schema": {"type": "string"}},
            },
        }
    },
)
async def get_dashboard(
    days: int = Query(
        default=1,
        ge=1,
        le=90,
        description="聚合窗口天数; 默认 1d, 运营拉趋势用 7 / 30",
    ),
    format: str = Query(  # noqa: A002 - REST 风格参数名, 与 query 一致
        default="json",
        pattern="^(json|html)$",
        description="json (默认) | html (单页表格)",
    ),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """``GET /api/v1/admin/dashboard?days=7&format=html`` → 单页 HTML; 默认 JSON.

    spec/12 §BE-S5-006 灰度阶段轻量看板; 完整 Grafana / Superset 后置 5.5+.
    """
    metrics = await admin_dashboard_service.collect_metrics(session, days=days)
    payload = DashboardResponse.model_validate(metrics.to_dict())

    if format == "html":
        return HTMLResponse(content=_render_dashboard_html(payload))
    return Response(
        content=payload.model_dump_json(),
        media_type="application/json",
    )


# ─── Ops 用户管理 (Sprint 12 / 上线整合) ──────────────────────────────
# 设计动机: 短信资质未下来 + admin 13007458553 忘密码 → 自助登录死锁.
# 这里提供"运维通道"绕过 SMS, 直接重置密码并(可选)授权 admin. 跟 sprint 10
# /admin/users/* (JWT + is_admin 鉴权) 区别: 那条路是 in-app admin 用的 UI,
# 这条路是无 admin 可用时的"破冰"通道, 走 X-Admin-Token, 服务器侧凭票入场.
#
# 不放进 Sprint 10 的 admin_users.py: 那个文件依赖 get_current_admin (JWT),
# 跟这里的 X-Admin-Token 走两套鉴权; 强行混到一起会让"哪条路是哪种鉴权"很难读.


class OpsSetPasswordRequest(BaseModel):
    """Sprint 12 P0-1: ops 重置密码请求体.

    - new_password: 复用业务密码强度规则 (6-32 字, 至少 1 数字)
    - grant_admin: 默认 True (主用例: 解锁初始 admin); False 时不修改 is_admin,
      但**绝不降级** — 已经是 admin 的不会因为 grant_admin=False 被卸权,
      防误操作把唯一 admin 关在外面 (要降权请走 Sprint 10 /admin/users/{id}).
    """

    new_password: str = Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=f"新密码 {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} 字, 至少含 1 数字",
    )
    grant_admin: bool = Field(
        default=True,
        description="是否同时 grant is_admin=true; False 时不动 is_admin (但绝不卸权)",
    )

    @model_validator(mode="after")
    def _validate_format(self) -> Self:
        _validate_password_format(self.new_password)
        return self


class OpsSetPasswordResponse(BaseModel):
    """Sprint 12 P0-1: ops 重置密码响应."""

    user_id: str
    phone_masked: str = Field(description="脱敏手机号 (运维侧只看脱敏即可)")
    is_admin: bool
    message: str = Field(
        default="Password reset successful.",
        description="提示运维下一步动作",
    )
    security_warning: str = Field(
        default=(
            "旧 access token 在自然 30min TTL 内仍可用; 旧 refresh token 在 "
            "30day TTL 内仍可换新 access. 主用例 (admin 自助解锁) 安全, 但安全事件强踢需要 "
            "后续 sprint 加 password_version JWT claim 才能实现 (见 docs/bug/2026.05.21.md P2)."
        ),
        description="诚实告知运维当前实现的安全边界",
    )


@router.post(
    "/users/by-phone/{phone}/set-password",
    response_model=OpsSetPasswordResponse,
    dependencies=[Depends(require_admin_token)],
    summary="ops 通道直接重置用户密码 (绕过 SMS); 用于 admin 自助登录死锁兜底",
    responses={
        401: {"description": "X-Admin-Token 缺失或不匹配"},
        404: {"description": "phone 没找到对应 user"},
        503: {"description": "服务器没配 OPS_ADMIN_TOKEN"},
    },
)
async def ops_set_user_password(
    payload: OpsSetPasswordRequest,
    phone: str = Path(
        ...,
        description="目标用户手机号; 接受 13xxx (默认 +86) 或 +8613xxx; URL 中 + 要 encode 为 %2B",
        min_length=8,
        max_length=20,
    ),
    session: AsyncSession = Depends(get_session),
) -> OpsSetPasswordResponse:
    """Sprint 12 P0-1: 凭 X-Admin-Token 直接重置任意用户密码.

    使用场景:
    - 初始 admin (13007458553) 忘密码 + 短信资质未下来 → 唯一解锁通道
    - 用户主动求助"我手机收不到验证码也忘密码了" → 运维人工核身后重置
    - 安全事件后强制重置某用户 + 踢下线所有设备

    不走任何用户 JWT, 不需要被重置用户在线; 仅依赖 server 侧 OPS_ADMIN_TOKEN.

    副作用:
    1. password_hash 覆盖 (bcrypt cost=12)
    2. (可选) is_admin 置 true; 永不卸权
    3. 写一条 audit logger.info (Sprint 11 上线 audit_log 表后会持久化到 DB)

    安全边界 (诚实声明):
    - 旧 access token 在 JWT 自然 30min TTL 内仍可用 (无 password_version claim 机制)
    - 旧 refresh token 在 30day TTL 内仍可换新 access (refresh blacklist 走 Redis 单
      条 jti 撤销, 没有"该用户所有 refresh 一键失效"的机制; auth_sessions 表存在但
      auth flow 未主动写入)
    - 主用例 (admin 自助解锁登录) 不需要踢任何人, 这层"旧 token 仍可用"反而是优点
      (admin 设备没换, 旧 access 顺其自然到期; ta 用新密码再登一次拿新 token 即可)
    - 安全事件强踢需要后续 sprint 加 ``users.password_version`` + JWT ``pv`` claim
      + 解码时校验 pv ≥ user.password_version. 当前不在 P0-1 范围.

    安全: 这是 P0 高敏感接口. 任何拿到 OPS_ADMIN_TOKEN 的人 = 能改任意用户密码.
    OPS_ADMIN_TOKEN 长度 ≥ 32 byte 随机串 + chmod 600 .env + 不进 git.
    """
    normalized_phone = normalize_phone(phone)
    masked = mask_phone(normalized_phone)

    target = (
        await session.execute(select(User).where(User.phone == normalized_phone))
    ).scalar_one_or_none()
    if target is None:
        logger.warning(
            f"ops.set_password.user_not_found phone={masked}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "user_not_found",
                "message": f"未找到 phone={masked} 对应用户",
            },
        )

    new_hash = hash_password(payload.new_password)
    target.password_hash = new_hash
    if payload.grant_admin and not target.is_admin:
        target.is_admin = True

    await session.commit()

    logger.warning(
        f"ops.set_password.ok user_id={target.user_id} phone={masked} "
        f"is_admin={target.is_admin} grant_admin={payload.grant_admin}"
    )
    return OpsSetPasswordResponse(
        user_id=str(target.user_id),
        phone_masked=masked,
        is_admin=target.is_admin,
    )


__all__ = ["router"]
