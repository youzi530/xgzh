"""BE-S5-002 PIPL PII inventory admin 接口 schemas.

对应 ``app.services.compliance.pii_inventory`` 静态清单 + DB 实时计数.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PIIItemSchema(BaseModel):
    """单条 PII 字段声明 (与 ``PIIItem`` dataclass 一一对应)."""

    field: str = Field(..., description="ORM 列名")
    table: str = Field(
        ..., description="ORM 表名; ``__log__`` 表示日志类 PII (无 ORM 表)"
    )
    scenario: str = Field(..., description="收集场景 (中文)")
    purpose: str = Field(..., description="业务用途 (中文)")
    legal_basis: str = Field(
        ...,
        description=(
            "PIPL §13 合法性基础: contract_necessity / consent / legal_obligation / "
            "public_interest / vital_interest / publicly_available / legitimate_interest"
        ),
    )
    retention_days_after_logout: int = Field(
        ...,
        description=(
            "注销后保留天数; 0 = 立即清, > 0 = 用于风控 / 对账, 90 = 日志归档上限"
        ),
    )
    is_sensitive: bool = Field(
        default=False, description="PIPL §28 敏感个人信息 (手机号 / 身份证 / 生物特征)"
    )
    notes: str | None = Field(default=None, description="额外备注")


class ThirdPartySDKSchema(BaseModel):
    """第三方 SDK 收集 PII 声明 (PIPL §23-25 共同处理者)."""

    name: str = Field(..., description="SDK 名称")
    vendor: str = Field(..., description="提供方")
    purpose: str = Field(..., description="对接用途")
    pii_collected: str = Field(..., description="该 SDK 收集的 PII 字段")
    url: str = Field(..., description="SDK 隐私政策链接")


class ConsentMechanismSchema(BaseModel):
    """同意机制声明 (PIPL §14)."""

    type: str = Field(..., description="explicit_opt_in / implicit / etc.")
    ui_location: str = Field(..., description="同意 UI 在哪呈现")
    rejection_behavior: str = Field(..., description="用户拒绝同意时的处理")
    withdrawal_path: str = Field(..., description="用户撤回同意的路径")


class DBCountsSchema(BaseModel):
    """实时数据规模 (admin 审计 / 法务报告引用)."""

    total_active_users: int = Field(
        ..., description="当前 status=1 且未注销的用户数"
    )
    total_users_lifetime: int = Field(
        ..., description="历史累计用户数 (含注销 / 禁用)"
    )
    total_push_tokens: int = Field(..., description="活跃推送 token 数")
    total_feedbacks_with_ip: int = Field(
        ..., description="带 IP 的反馈记录数 (BE-S5-004)"
    )
    total_auth_sessions: int = Field(..., description="活跃 refresh token 数")


class PIIInventoryResponse(BaseModel):
    """``GET /api/v1/admin/pii-inventory`` 响应."""

    items: list[PIIItemSchema] = Field(..., description="PII 字段清单")
    data_export_jurisdictions: list[str] = Field(
        default_factory=list,
        description="数据出境法域 (PIPL §38-40); MVP 不出境 → []",
    )
    consent_mechanism: ConsentMechanismSchema = Field(
        ..., description="用户同意机制声明 (PIPL §14)"
    )
    third_party_sdks: list[ThirdPartySDKSchema] = Field(
        ..., description="第三方 SDK 共同处理者声明"
    )
    counts: DBCountsSchema = Field(..., description="实时数据规模 (DB 计数)")
    spec_version: str = Field(
        default="2026-04-spec-12-BE-S5-002",
        description="清单 spec 版本; 更新清单时 bump",
    )


__all__ = [
    "ConsentMechanismSchema",
    "DBCountsSchema",
    "PIIInventoryResponse",
    "PIIItemSchema",
    "ThirdPartySDKSchema",
]
