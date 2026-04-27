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
    {"partnership_type", "partnership_cpa_amount", "partnership_cps_rate"}
)


def to_public_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """剥掉 ``partnership_*`` 内部字段, 防止泄漏到 ``/api/v1/brokers/*``.

    ``BrokerPublic`` 设了 ``extra="forbid"``, 直接 ``model_validate(payload)``
    会因 partnership_* 报错; 路由层用本 helper 显式投影后再 model_validate.
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
