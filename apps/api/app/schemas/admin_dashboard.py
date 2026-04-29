"""BE-S5-006 admin/dashboard JSON 响应 schema.

与 ``app/services/admin_dashboard_service.py`` 的 dataclass 一一对应; service 层吐
``DashboardMetrics.to_dict()`` 后, 路由层 ``model_validate`` 进 Pydantic 做严格校验
(便于 OpenAPI 文档 / 客户端代码生成 / API 兼容性回归).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserActivityPayload(BaseModel):
    distinct_active_users: int = Field(ge=0)


class RegistrationPayload(BaseModel):
    new_users_in_window: int = Field(ge=0)
    total_users_lifetime: int = Field(ge=0)


class VipConversionPayload(BaseModel):
    total_memberships: int = Field(ge=0)
    trial_memberships: int = Field(ge=0)
    active_paid_memberships: int = Field(ge=0)
    expired_memberships: int = Field(ge=0)
    trial_to_paid_pct: float = Field(ge=0.0)


class AgentUsagePayload(BaseModel):
    sessions_in_window: int = Field(ge=0)
    user_messages_in_window: int = Field(ge=0)
    llm_calls_in_window: int = Field(ge=0)
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_cost_cny: float = Field(ge=0.0)


class ErrorRatePayload(BaseModel):
    window_seconds: int = Field(ge=0)
    total_requests: int = Field(ge=0)
    total_errors: int = Field(ge=0)
    error_pct: float = Field(ge=0.0)


class LLMPerformancePayload(BaseModel):
    avg_input_tokens_per_call: float = Field(ge=0.0)
    avg_output_tokens_per_call: float = Field(ge=0.0)
    avg_cost_cny_per_call: float = Field(ge=0.0)


class DashboardResponse(BaseModel):
    """``GET /api/v1/admin/dashboard?format=json`` 顶层响应."""

    window_days: int = Field(ge=1, le=90)
    user_activity: UserActivityPayload
    registration: RegistrationPayload
    vip_conversion: VipConversionPayload
    agent_usage: AgentUsagePayload
    error_rate: ErrorRatePayload
    llm_performance: LLMPerformancePayload


__all__ = [
    "AgentUsagePayload",
    "DashboardResponse",
    "ErrorRatePayload",
    "LLMPerformancePayload",
    "RegistrationPayload",
    "UserActivityPayload",
    "VipConversionPayload",
]
