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
| GET    | /api/v1/admin/feedbacks       | 反馈列表 (分页 + filter)            |
| GET    | /api/v1/admin/pii-inventory   | PIPL 个人信息收集清单 + 实时计数    |
| GET    | /api/v1/admin/dashboard       | 6 指标数据看板 (?days&format)       |

注意:
- 所有路由都 ``require_admin_token`` Depends, ``OPS_ADMIN_TOKEN`` 留空时返 503
- ``flags`` 与 ``metrics`` 的语义都是"运维触达, 不在用户产品里出现"; 不在 OpenAPI
  schema 上设 ``include_in_schema=False`` 是为了让 ops 的同事能从 ``/docs`` 直接试,
  生产实际靠"未配 OPS_ADMIN_TOKEN → 503"做最后保险。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas.admin_dashboard import DashboardResponse
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


__all__ = ["router"]
