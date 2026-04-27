"""Broker 业务 service 层 (BE-S3-007 横向对比 API).

2 个端点的业务逻辑:

1. ``list_brokers``: 列表 + 3 维筛选 (market_support / partnership_type / is_active) +
   排序 (display_order DESC, created_at DESC); 不分页 (券商总数 < 30, 一次性拉全)
2. ``get_broker_detail``: 按 ``slug`` 取详情 (URL 友好 → ``/api/v1/brokers/futubull``)

缓存策略
========
- 列表: ``@cached(ttl=600, namespace="brokers:list")`` — 10 min, 券商基础信息
  极少变 (运营手动调价 / 调权重时显式调 ``invalidate_namespace``); 与文章列表
  5 min 区分: 文章实时性比券商高
- 详情: ``@cached(ttl=600, namespace="brokers:detail")``
- 写入端 (运营后台 PATCH 券商) 调 ``invalidate_namespace("brokers:list")``
  + ``invalidate_namespace("brokers:detail")``; 当前 Sprint 3 暂无运营后台,
  以 ``seed_brokers.py`` 脚本写入为主, 脚本运行时也会显式失效缓存

partnership_* 隔离
==================
service 层始终返带 partnership_* 的完整 dict (BrokerInternal 形态), 路由层用
``BrokerPublic.model_validate`` 自然丢弃这三字段 (extra="forbid" 会报错 ?
不会 — 因为 BrokerPublic 没声明这三字段, model_validate 默认忽略 ORM 上多余
字段; 见 from_attributes=True 行为). 这样 service 单层 dict 即满足 routes
返 Public + 内部 admin 路由复用 Internal 两条路径.

为什么返 ``dict[str, Any]`` 而非 Pydantic 实例
================================================
``@cached`` 用 ``json.dumps`` 写缓存 + ``json.loads`` 读, Pydantic 实例不能
直接走; 与 ``article_service`` / ``ipo_service`` 同方案.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache import cached
from app.db import get_session_factory
from app.db.models import Broker

LIST_CACHE_TTL_SECONDS: Final[int] = 600
DETAIL_CACHE_TTL_SECONDS: Final[int] = 600

MarketFilter = Literal["HK", "A", "US", "SG", "all"]
PartnershipFilter = Literal["CPA", "CPS", "BOTH", "NONE", "all"]


def _orm_to_dict(broker: Broker) -> dict[str, Any]:
    """``Broker`` ORM → JSON-friendly dict (含 partnership_*; 路由层选择性投影).

    Decimal / UUID / datetime 用 str/float 兜底, 与 ``article_service._orm_to_dict``
    同款; 路由层 ``BrokerPublic.model_validate`` 时会再转回各自类型.
    """
    return {
        "broker_id": str(broker.broker_id),
        "slug": broker.slug,
        "name_zh": broker.name_zh,
        "name_en": broker.name_en,
        "logo_url": broker.logo_url,
        "market_support": list(broker.market_support or []),
        "licenses": list(broker.licenses or []),
        "fees": dict(broker.fees or {}),
        "features": dict(broker.features or {}),
        "promotion": dict(broker.promotion or {}),
        "partnership_type": broker.partnership_type,
        "partnership_cpa_amount": (
            float(broker.partnership_cpa_amount)
            if broker.partnership_cpa_amount is not None
            else None
        ),
        "partnership_cps_rate": (
            float(broker.partnership_cps_rate)
            if broker.partnership_cps_rate is not None
            else None
        ),
        "display_order": int(broker.display_order),
        "is_active": bool(broker.is_active),
        "created_at": broker.created_at.isoformat(),
        "updated_at": broker.updated_at.isoformat(),
    }


# ─── 1. list_brokers ──────────────────────────────────────────────────────


async def _list_brokers_db(
    factory: async_sessionmaker[AsyncSession],
    *,
    market: MarketFilter,
    partnership: PartnershipFilter,
    only_active: bool,
) -> list[dict[str, Any]]:
    """构造查询 + 拉全量结果 (券商总数 < 30, 不分页).

    market 走 JSONB ``@>`` (e.g. ``market_support @> '["HK"]'::jsonb``); 命中
    GIN 索引方向虽然 brokers 表没建 GIN 索引 (体量小), 但 ``@>`` 在小表上
    seq scan 也 < 1ms.

    SoftDelete: ORM 默认查询不会自动过滤 ``deleted_at IS NOT NULL`` (与 users
    表同), service 层显式加.
    """
    stmt = select(Broker).where(Broker.deleted_at.is_(None))
    if only_active:
        stmt = stmt.where(Broker.is_active.is_(True))
    if market != "all":
        stmt = stmt.where(
            sa_text("market_support @> CAST(:m AS jsonb)").bindparams(
                m=f'["{market}"]'
            )
        )
    if partnership != "all":
        stmt = stmt.where(Broker.partnership_type == partnership)

    stmt = stmt.order_by(Broker.display_order.desc(), Broker.created_at.desc())

    async with factory() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [_orm_to_dict(r) for r in rows]


@cached(ttl_seconds=LIST_CACHE_TTL_SECONDS, namespace="brokers:list")
async def list_brokers(
    *,
    market: MarketFilter = "all",
    partnership: PartnershipFilter = "all",
    only_active: bool = True,
) -> dict[str, Any]:
    """券商列表 (含 partnership_*, 路由层投影 Public).

    ``only_active=True`` 默认隐藏运营临时下架的券商 (``is_active=False``);
    Sprint 4 运营后台需要 ``only_active=False`` 看下架历史时再开放.
    """
    factory = get_session_factory()
    items = await _list_brokers_db(
        factory,
        market=market,
        partnership=partnership,
        only_active=only_active,
    )
    return {"items": items, "total": len(items)}


# ─── 2. get_broker_detail ──────────────────────────────────────────────────


@cached(ttl_seconds=DETAIL_CACHE_TTL_SECONDS, namespace="brokers:detail")
async def get_broker_detail(slug: str) -> dict[str, Any] | None:
    """券商详情 by slug. 不存在返 ``None``, 路由层 → 404.

    包含软删: ``deleted_at IS NOT NULL`` 也返 None (历史 ConversionEvent 仍可
    通过 broker_id 关联, 但用户查询面不该看到下架券商).
    """
    if not slug or not slug.strip():
        return None

    factory = get_session_factory()
    async with factory() as session:
        broker = (
            await session.execute(
                select(Broker).where(
                    Broker.slug == slug.strip(),
                    Broker.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if broker is None:
            return None

    return _orm_to_dict(broker)
