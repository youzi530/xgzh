"""Broker 域 Pydantic 模型 (BE-S3-007 横向对比 API).

设计要点
========
- ``BrokerPublic`` 公开字段: 所有 7 个 JSONB + 元数据, 不含 ``partnership_*``
- ``BrokerInternal`` 内部字段: 在 Public 基础上加 partnership 三元组 (CPA / CPS / 财务对账用)
- 显式 ``model_config = ConfigDict(extra="forbid", from_attributes=True)`` 与 ``article.py``
  保持一致 (extra=forbid 是后端 BaseModel 通行标准, ORM → Pydantic 用 from_attributes=True)
- ``BrokerListResponse`` 用 dict 持久化于 Redis, 故 ``items: list[dict]`` 而非
  ``list[BrokerPublic]`` (Pydantic 实例不能 json.dumps 直接落 Redis); 路由层再
  ``BrokerPublic.model_validate`` 重构, 与 ``ArticleListResponse`` 同构

JSONB 字段
==========
``market_support`` / ``licenses`` / ``fees`` / ``features`` / ``promotion`` 都
保留 ``Any``: 各券商 schema 不一 (HK 才有 hk_commission_rate, A 股专门
a_commission_rate), Pydantic 强校验弊大于利 — 数据校验放 seed_brokers.py
+ DB CHECK 双层兜底.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PartnershipType = Literal["CPA", "CPS", "BOTH", "NONE"]
BrokerSortBy = Literal["display_order", "created_at"]

INTERNAL_FIELDS: frozenset[str] = frozenset(
    {
        "partnership_type",
        "partnership_cpa_amount",
        "partnership_cps_rate",
        # Sprint 11: admin 才看的字段; 公开路径剥掉 (service _orm_to_dict 已带, 跟
        # partnership_* 同款防御 in depth — 即便忘记调 to_public_dict, BrokerPublic
        # extra=forbid 也会 raise)
        "is_deleted",
        "deleted_at",
    }
)


def to_public_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """剥掉 ``partnership_*`` + admin 字段 (Sprint 11), 防止泄漏到 ``/api/v1/brokers/*``.

    ``BrokerPublic`` 设了 ``extra="forbid"``, 直接 ``model_validate(payload)``
    会因 partnership_* / is_deleted 报错; 路由层用本 helper 显式投影后再 model_validate.
    """
    return {k: v for k, v in payload.items() if k not in INTERNAL_FIELDS}


class BrokerPublic(BaseModel):
    """对外暴露的券商信息 (列表 + 详情 共享).

    隔离 ``partnership_*`` 三字段 → 端不能感知我方的财务返佣条款.
    若运营误把 ``partnership_*`` 返到 FE, ``extra="forbid"`` 会让 Pydantic
    重构期 raise — 防御 in depth.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    broker_id: UUID
    slug: str = Field(min_length=1, max_length=32)
    name_zh: str
    name_en: str | None = None
    logo_url: str | None = None

    market_support: list[str] = Field(default_factory=list)
    licenses: list[str] = Field(default_factory=list)
    fees: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    promotion: dict[str, Any] = Field(default_factory=dict)
    open_account_url: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "顶层开户链接 (Sprint 11). admin 编辑入口; 与 promotion.referral_url 双字段并存; "
            "FE redirect 优先用本字段, fallback JSONB. 长期稳定不受 promotion 生命周期影响"
        ),
    )

    display_order: int
    is_active: bool

    created_at: datetime
    updated_at: datetime


class BrokerInternal(BrokerPublic):
    """运营 / 财务对账后台用; 含 ``partnership_*`` 三字段, 永不出 ``/api/v1/brokers/*``.

    BE-S3-008 redirect 端点会读 ``partnership_type`` 决定是否埋 utm_campaign,
    但端点本身只返 302, 不暴露这些字段.
    Sprint 4 财务对账后台 (走单独 admin 路由 + admin auth) 才直接暴露此 schema.
    """

    partnership_type: PartnershipType = "NONE"
    partnership_cpa_amount: Decimal | None = None
    partnership_cps_rate: Decimal | None = None


class BrokerListResponse(BaseModel):
    """``GET /api/v1/brokers`` 列表返回结构."""

    model_config = ConfigDict(extra="forbid")

    items: list[BrokerPublic]
    total: int = Field(ge=0)


# ─── Sprint 11 Module A: admin CRUD schemas ─────────────────────────────


class BrokerAdminDetail(BrokerInternal):
    """admin 视角 broker 详情. 含 partnership_* + 标记是否软删.

    与 ``BrokerInternal`` 的区别: 加 ``is_deleted`` / ``deleted_at`` 让 admin 排查软删行.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    is_deleted: bool = False
    deleted_at: datetime | None = None


class BrokerAdminListResponse(BaseModel):
    """admin 视角 broker 列表 (不分页, 券商总数 < 30)."""

    model_config = ConfigDict(extra="forbid")

    items: list[BrokerAdminDetail]
    total: int = Field(ge=0)


class BrokerCreate(BaseModel):
    """``POST /admin/brokers``. slug 必填, 其余可选."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
        min_length=2,
        max_length=32,
        description="URL slug: 小写字母数字 + 连字符; 首尾必须字母数字",
    )
    name_zh: str = Field(min_length=1, max_length=64)
    name_en: str | None = Field(default=None, max_length=64)
    logo_url: str | None = Field(default=None, max_length=500)
    open_account_url: str | None = Field(default=None, max_length=500)
    market_support: list[Literal["HK", "A", "US", "SG"]] = Field(default_factory=list)
    licenses: list[str] = Field(default_factory=list)
    fees: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    promotion: dict[str, Any] = Field(default_factory=dict)
    partnership_type: PartnershipType = "NONE"
    partnership_cpa_amount: Decimal | None = Field(default=None, ge=0)
    partnership_cps_rate: Decimal | None = Field(default=None, ge=0, le=1)
    display_order: int = Field(default=0, ge=0, le=9999)
    is_active: bool = True


class BrokerUpdate(BaseModel):
    """``PATCH /admin/brokers/{slug}``. 全字段可选, JSONB 字段走 merge (key 级).

    与 ``BrokerCreate`` 主要差异:
    - 所有字段可选 (None = 不动)
    - JSONB 用 ``*_patch`` 后缀字段, 服务层浅 merge (admin 不易"整 dict 覆盖"误删 key)
    - slug 不允许改 (改 slug 会破坏外链 + conversion_events 历史归因; 想换名建新 broker)
    """

    model_config = ConfigDict(extra="forbid")

    name_zh: str | None = Field(default=None, min_length=1, max_length=64)
    name_en: str | None = Field(default=None, max_length=64)
    logo_url: str | None = Field(default=None, max_length=500)
    open_account_url: str | None = Field(default=None, max_length=500)
    market_support: list[Literal["HK", "A", "US", "SG"]] | None = None
    licenses: list[str] | None = None
    display_order: int | None = Field(default=None, ge=0, le=9999)
    is_active: bool | None = None
    partnership_type: PartnershipType | None = None
    partnership_cpa_amount: Decimal | None = Field(default=None, ge=0)
    partnership_cps_rate: Decimal | None = Field(default=None, ge=0, le=1)
    promotion_patch: dict[str, Any] | None = Field(
        default=None,
        description="JSONB merge: 传入 dict 跟现有 promotion 浅合并, 保留其它 key",
    )
    fees_patch: dict[str, Any] | None = None
    features_patch: dict[str, Any] | None = None
