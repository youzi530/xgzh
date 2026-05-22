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

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final, Literal

from loguru import logger
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache import cached, invalidate_namespace
from app.db import get_session_factory
from app.db.models import Broker

LIST_CACHE_TTL_SECONDS: Final[int] = 600
DETAIL_CACHE_TTL_SECONDS: Final[int] = 600

MarketFilter = Literal["HK", "A", "US", "SG", "all"]
PartnershipFilter = Literal["CPA", "CPS", "BOTH", "NONE", "all"]


# ─── Sprint 11 admin CRUD 自定义异常 ─────────────────────────────────────


class BrokerNotFoundError(Exception):
    """目标 slug 不存在或已软删 (admin 查/改/删时)."""


class BrokerSlugTakenError(Exception):
    """新建 / 改 slug 时 UNIQUE 冲突."""


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
        "open_account_url": broker.open_account_url,  # Sprint 11
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
        # Sprint 11: 软删信息. 公开路径 to_public_dict 会剥掉 (跟 partnership_* 同款)
        "is_deleted": broker.deleted_at is not None,
        "deleted_at": broker.deleted_at.isoformat() if broker.deleted_at else None,
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


# ─── Sprint 11 Module A: admin CRUD ──────────────────────────────────────
#
# 设计原则:
# - 跟 list_brokers / get_broker_detail 走完全不同的路径 (无 @cached, 直接 session 操作):
#   admin 写完立即 invalidate cache, 不能让 admin 自己看到 stale 数据
# - 跟 admin_user_service.py 风格对齐: dataclass error / async def / loguru
# - service 层抛业务 exception, 路由层 catch → HTTPException; 不直接抛 HTTP
# - 不在 service 调 logger 记 admin_audit_logs — Sprint 11 Module E 加 audit_service
#   后, 在路由层 wrap (因为 audit 需要 request.ip/user_agent, service 不该感知 HTTP)


async def admin_list_brokers(
    session: AsyncSession,
    *,
    include_deleted: bool = False,
    include_inactive: bool = True,
) -> list[dict[str, Any]]:
    """admin 视角的全量 broker 列表 (含下架 + 可选含软删).

    与公开 ``list_brokers`` 的区别:
    - 不走 cache (admin 写完即看, 不能 stale)
    - 不接 market/partnership filter (admin 管理页用 FE 端 filter, 量小; 后续可加)
    - ``include_deleted=True`` 时返已软删的行 (admin 排查 / 恢复用)
    - ``include_inactive=True`` 默认 (admin 默认看下架的)
    """
    stmt = select(Broker)
    if not include_deleted:
        stmt = stmt.where(Broker.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(Broker.is_active.is_(True))
    stmt = stmt.order_by(Broker.display_order.desc(), Broker.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [_orm_to_dict(r) for r in rows]


async def admin_get_broker(
    session: AsyncSession, slug: str, *, include_deleted: bool = True
) -> dict[str, Any]:
    """admin 视角查单 broker. 默认 include_deleted=True (排查软删的).

    Raises:
        BrokerNotFoundError: slug 不存在 (即便 include_deleted=True 也找不到)
    """
    stmt = select(Broker).where(Broker.slug == slug.strip())
    if not include_deleted:
        stmt = stmt.where(Broker.deleted_at.is_(None))
    broker = (await session.execute(stmt)).scalar_one_or_none()
    if broker is None:
        raise BrokerNotFoundError(f"broker slug={slug!r} not found")
    return _orm_to_dict(broker)


async def create_broker(
    session: AsyncSession,
    *,
    slug: str,
    name_zh: str,
    name_en: str | None = None,
    logo_url: str | None = None,
    market_support: list[str] | None = None,
    licenses: list[str] | None = None,
    fees: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
    promotion: dict[str, Any] | None = None,
    open_account_url: str | None = None,
    partnership_type: str = "NONE",
    partnership_cpa_amount: Decimal | None = None,
    partnership_cps_rate: Decimal | None = None,
    display_order: int = 0,
    is_active: bool = True,
) -> dict[str, Any]:
    """新建 broker. slug 冲突抛 ``BrokerSlugTakenError``.

    JSONB 字段默认空容器 ([] / {}); promotion / fees / features 用调用方传入的整 dict
    替换 (不 merge — 新建场景, 没有"已有 + patch" 语义).
    """
    broker = Broker(
        slug=slug.strip(),
        name_zh=name_zh.strip(),
        name_en=name_en.strip() if name_en else None,
        logo_url=logo_url.strip() if logo_url else None,
        market_support=market_support or [],
        licenses=licenses or [],
        fees=fees or {},
        features=features or {},
        promotion=promotion or {},
        open_account_url=open_account_url.strip() if open_account_url else None,
        partnership_type=partnership_type,
        partnership_cpa_amount=partnership_cpa_amount,
        partnership_cps_rate=partnership_cps_rate,
        display_order=display_order,
        is_active=is_active,
    )
    session.add(broker)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        # uq_brokers_slug 冲突 (msg 含 "uq_brokers_slug" 或 PG 23505)
        if "uq_brokers_slug" in str(e.orig).lower() or "duplicate key" in str(e.orig).lower():
            raise BrokerSlugTakenError(f"slug={slug!r} 已被占用") from e
        raise
    await session.refresh(broker)
    await invalidate_namespace("brokers:list", "brokers:detail")
    logger.info(
        f"broker.create.ok slug={broker.slug} name_zh={broker.name_zh} "
        f"open_account_url_set={broker.open_account_url is not None}"
    )
    return _orm_to_dict(broker)


async def update_broker(
    session: AsyncSession,
    *,
    slug: str,
    name_zh: str | None = None,
    name_en: str | None = None,
    logo_url: str | None = None,
    open_account_url: str | None = None,
    display_order: int | None = None,
    is_active: bool | None = None,
    market_support: list[str] | None = None,
    licenses: list[str] | None = None,
    promotion_patch: dict[str, Any] | None = None,
    fees_patch: dict[str, Any] | None = None,
    features_patch: dict[str, Any] | None = None,
    partnership_type: str | None = None,
    partnership_cpa_amount: Decimal | None = None,
    partnership_cps_rate: Decimal | None = None,
) -> dict[str, Any]:
    """PATCH broker. 标量字段直接 set; JSONB 字段走 merge (传入 dict 跟现有 dict
    浅合并, 只覆盖传入的 key, 其它 key 保留).

    传 ``None`` = 不动该字段 (与 Pydantic ``exclude_unset`` 配合); 想清空字符串字段
    显式传 ``""``.

    Raises:
        BrokerNotFoundError: slug 不存在或已软删 (软删的不允许 patch — admin 需要先恢复)
    """
    broker = (
        await session.execute(
            select(Broker).where(
                Broker.slug == slug.strip(),
                Broker.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if broker is None:
        raise BrokerNotFoundError(f"broker slug={slug!r} not found or deleted")

    changed: list[str] = []

    def _set_if_provided(attr: str, value: Any) -> None:
        if value is not None:
            current = getattr(broker, attr)
            new_value = value.strip() if isinstance(value, str) else value
            if isinstance(new_value, str) and new_value == "":
                new_value = None  # 空字符串视为清空
            if current != new_value:
                setattr(broker, attr, new_value)
                changed.append(attr)

    _set_if_provided("name_zh", name_zh)
    _set_if_provided("name_en", name_en)
    _set_if_provided("logo_url", logo_url)
    _set_if_provided("open_account_url", open_account_url)
    _set_if_provided("display_order", display_order)
    _set_if_provided("is_active", is_active)
    _set_if_provided("partnership_type", partnership_type)
    _set_if_provided("partnership_cpa_amount", partnership_cpa_amount)
    _set_if_provided("partnership_cps_rate", partnership_cps_rate)

    if market_support is not None and list(broker.market_support or []) != market_support:
        broker.market_support = market_support
        changed.append("market_support")
    if licenses is not None and list(broker.licenses or []) != licenses:
        broker.licenses = licenses
        changed.append("licenses")

    for json_attr, patch in (
        ("promotion", promotion_patch),
        ("fees", fees_patch),
        ("features", features_patch),
    ):
        if patch is not None:
            merged = {**(getattr(broker, json_attr) or {}), **patch}
            if merged != getattr(broker, json_attr):
                setattr(broker, json_attr, merged)
                changed.append(json_attr)

    if not changed:
        logger.info(f"broker.update.noop slug={slug}")
        return _orm_to_dict(broker)

    await session.commit()
    await session.refresh(broker)
    await invalidate_namespace("brokers:list", "brokers:detail")
    logger.info(f"broker.update.ok slug={slug} fields={changed}")
    return _orm_to_dict(broker)


async def soft_delete_broker(
    session: AsyncSession, *, slug: str
) -> dict[str, Any]:
    """软删 broker (deleted_at = now). 已软删的视为幂等成功.

    Raises:
        BrokerNotFoundError: slug 物理不存在
    """
    broker = (
        await session.execute(select(Broker).where(Broker.slug == slug.strip()))
    ).scalar_one_or_none()
    if broker is None:
        raise BrokerNotFoundError(f"broker slug={slug!r} not found")
    if broker.deleted_at is None:
        broker.deleted_at = datetime.now(UTC)
        broker.is_active = False
        await session.commit()
        await session.refresh(broker)
        await invalidate_namespace("brokers:list", "brokers:detail")
        logger.warning(f"broker.soft_delete.ok slug={slug}")
    else:
        logger.info(f"broker.soft_delete.noop slug={slug} already_deleted")
    return _orm_to_dict(broker)


async def restore_broker(
    session: AsyncSession, *, slug: str
) -> dict[str, Any]:
    """恢复软删的 broker (deleted_at=NULL, is_active=True).

    Sprint 11 spec 没要求, 但运维需要这条路径 (admin 误删后恢复). 幂等 — 没软删的
    broker restore 是 no-op.

    Raises:
        BrokerNotFoundError: slug 物理不存在
    """
    broker = (
        await session.execute(select(Broker).where(Broker.slug == slug.strip()))
    ).scalar_one_or_none()
    if broker is None:
        raise BrokerNotFoundError(f"broker slug={slug!r} not found")
    if broker.deleted_at is not None:
        broker.deleted_at = None
        broker.is_active = True
        await session.commit()
        await session.refresh(broker)
        await invalidate_namespace("brokers:list", "brokers:detail")
        logger.warning(f"broker.restore.ok slug={slug}")
    return _orm_to_dict(broker)
