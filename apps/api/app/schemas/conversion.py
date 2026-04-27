"""券商转化埋点相关 Pydantic 模型 (BE-S3-008).

3 个端点的请求/响应:
- ``GET /brokers/{slug}/redirect``  → 直接 302, 无 JSON body (但有错误响应 schema)
- ``GET /brokers/{slug}/stats``     → ``BrokerStats30d``
- ``POST /brokers/postback``        → 暂返 501 占位, 但定义 ``PostbackRequest`` 锁定将来契约

防刷 / IP / device 字段语义都有详细 docstring, 与 ``ConversionEvent`` ORM 完全对齐.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal["click", "signup", "kyc_pass", "deposit", "first_trade"]


class BrokerStats30d(BaseModel):
    """``GET /brokers/{slug}/stats`` 返回结构.

    30 天 GROUP BY event_type 计数; 走 ``ix_conversion_events_broker_event_created``
    索引 (broker_id, event_type, created_at DESC) 命中范围扫描.

    spec/03 §模块四 ``stats_30d`` 的语义: 用于运营 / VIP 用户看券商热度,
    不返个人级别的事件流.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str
    broker_id: str
    window_days: int = Field(default=30, ge=1, le=365)

    clicks: int = Field(ge=0, description="event_type='click' 计数")
    signups: int = Field(ge=0, description="event_type='signup' 计数 (未必落库, Postback 后填)")
    kyc_pass: int = Field(ge=0)
    deposits: int = Field(ge=0)
    first_trades: int = Field(ge=0)

    total_amount_cny: float = Field(
        ge=0.0,
        description=(
            "30d 累计入金 / 交易额 (CNY); 仅 attributed=TRUE 行计入, 防止未核销 "
            "事件污染统计"
        ),
    )


class PostbackRequest(BaseModel):
    """``POST /brokers/postback`` 请求体 (Sprint 4+ 才接, 本 PR 端点返 501).

    券商方签名后调本 API 通知后续事件 (signup / kyc_pass / deposit / first_trade).
    本 schema 提前锁定字段, 让后续 PR 直接接入即可, 不破坏契约.

    安全:
    - 端点必须走签名校验 (Sprint 4+ 加 ``X-Broker-Signature`` HMAC)
    - 频率限制: 单券商 100 req/min (本 PR 不实现)
    - 幂等: 同一 ``external_event_id`` 只落 1 行 (DB UNIQUE 由 Sprint 4+ 加)
    """

    model_config = ConfigDict(extra="forbid")

    broker_slug: str = Field(min_length=1, max_length=32)
    external_event_id: str = Field(
        min_length=1,
        max_length=128,
        description="券商方事件唯一 ID, 幂等键 (本 PR 占位, Sprint 4+ 加 UNIQUE 约束)",
    )
    event_type: EventType
    utm_campaign: str | None = Field(default=None, max_length=64)
    user_external_id: str | None = Field(
        default=None,
        max_length=128,
        description="券商方账户 ID (经 Postback 回填后, 走规则反查 user_id)",
    )
    amount_cny: float | None = Field(default=None, ge=0.0)


class PostbackResponse(BaseModel):
    """``POST /brokers/postback`` 响应 (Sprint 4+ 实装时返 200, 本 PR 不返这个 — 端点直接 501)."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    event_id: str | None = None
    message: str
