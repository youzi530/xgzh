"""券商转化埋点 service 层 (BE-S3-008).

3 个核心职能:
1. ``log_click_with_dedup``: 落 ``conversion_events`` (event_type='click'); 同
   (user_or_device, broker_id, utm_campaign) 1h 内仅 1 行, 防点击刷量
2. ``build_redirect_url``: 把 ``broker.promotion.referral_url`` 拼上 utm_source /
   utm_campaign / utm_medium / invite_code, 用 urlencode 防注入
3. ``get_broker_stats_30d``: GROUP BY event_type, 30d 范围统计

防刷设计 (核心)
================
key 优先级 (与 spec/10 §BE-S3-008 "防刷"段对齐):
1. 已登录: ``user_id``
2. 匿名 + 有 device_id: ``device_id``
3. 匿名 + 没 device_id: 取客户端 IP (X-Forwarded-For 第一段, fallback request.client.host)
4. 全 None: 退化为不防刷 (走"每次都落"路径; 不应在生产发生, 因为前端拦截器一定带 device_id)

实现用 ``incr_with_expire`` (Redis Lua 原子 INCR + 仅首次 EXPIRE):
- 第一次返 1 → 落库
- 第二次起返 > 1 → 不落库, 但 redirect 仍照常 302 (UX > 数据完整, 用户体验不能因防刷退化)

为什么不用 DB UNIQUE (broker_id, user_id/device_id, utm_campaign):
- click 事件是高频流水, DB UNIQUE 索引在写入端有锁竞争
- signup / deposit 等其他 event_type 不该限频, 加约束反而会误杀
- Redis 防刷成本 < 1ms, 与 PG INSERT 同时进行 (并发跑, 不串行)

URL 拼接安全
=============
``urlencode`` 处理特殊字符 (& / # / 中文); 不直接 f-string 拼接, 防 utm_campaign 含
``&malicious=evil`` 时绕过既有参数. 与 ``article_service.py`` JSON 注入修复一脉相承.

stats 缓存
==========
本 service 不缓存 stats — 30d 统计实时性要求高 (运营 / VIP 看转化漏斗时), 1 min
TTL 又会让"用户刚点击 → 即时看 stats" 体验差; PG 走索引足够快 (< 50ms typical).
将来 BE-S3-009 上 VIP 配额闸门后, 可以改成 60s TTL (访问频率上去后再缓存).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

from sqlalchemy import func, select

from app.cache import get_redis_client
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import Broker, ConversionEvent

CLICK_DEDUP_TTL_SECONDS: int = 3600  # 1 hour, spec/10 §BE-S3-008 明确
STATS_DEFAULT_WINDOW_DAYS: int = 30


# ─── 1. 防刷 + 落库 ───────────────────────────────────────────────────────


def _click_dedup_key(
    *,
    broker_id: uuid.UUID,
    actor_key: str,
    utm_campaign: str | None,
) -> str:
    """同 (broker, actor, utm_campaign) 1h 内仅落 1 行 click 事件的 dedup key.

    actor_key 由调用侧决定来自 user_id / device_id / IP — service 层不关心,
    只把它当 opaque string 拼到 key 里. namespace 加 ``conversion:click:`` 前缀
    防与其他 cache 冲突 (与 ``invalidate_namespace("brokers:list" / "brokers:detail")``
    都走 ``cache:<ns>:`` 格式区分).
    """
    return f"conversion:click:{broker_id}:{actor_key}:{utm_campaign or '-'}"


async def log_click_with_dedup(
    *,
    broker: Broker,
    actor_key: str | None,
    user_id: uuid.UUID | None,
    device_id: str,
    utm_campaign: str | None,
    utm_medium: str | None,
    referer: str | None,
    ip_addr: str | None,
    user_agent: str | None,
) -> bool:
    """1h 防刷窗口内幂等落 click 事件; 返 True 表示本次落入了 DB.

    若 ``actor_key`` 为 None (生产不应发生), 不走 Redis 直接落库 (兜底).
    """
    if actor_key is not None:
        redis = get_redis_client()
        try:
            cnt = await redis.incr_with_expire(
                _click_dedup_key(
                    broker_id=broker.broker_id,
                    actor_key=actor_key,
                    utm_campaign=utm_campaign,
                ),
                CLICK_DEDUP_TTL_SECONDS,
            )
            if cnt > 1:
                logger.info(
                    f"conversion.click.dedup_hit broker={broker.slug} "
                    f"actor={actor_key} utm_campaign={utm_campaign} count={cnt}"
                )
                return False
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"conversion.click.dedup_redis_fail broker={broker.slug} err={e}"
            )
            # fail-open: Redis 抖动时还是落库, 数据可能多 1-2 行 但不丢点击

    factory = get_session_factory()
    async with factory() as session, session.begin():
        evt = ConversionEvent(
            user_id=user_id,
            device_id=device_id,
            broker_id=broker.broker_id,
            event_type="click",
            utm_source="xgzh",
            utm_campaign=utm_campaign,
            utm_medium=utm_medium,
            referer=referer,
            ip_addr=ip_addr,
            user_agent=user_agent,
            amount_cny=None,
            attributed=False,
        )
        session.add(evt)
    return True


# ─── 2. URL 拼接 ──────────────────────────────────────────────────────────


def build_redirect_url(
    broker: Broker,
    *,
    utm_campaign: str | None = None,
    utm_medium: str | None = None,
) -> str | None:
    """Broker 开户链接 + 拼 utm_source / campaign / medium / invite_code.

    Args:
        broker: 已确认 ``deleted_at IS NULL AND is_active=True`` 的 Broker
        utm_campaign: 端点 query 透传; None 时不落 utm_campaign 参数
        utm_medium: 端点 query 透传

    Returns:
        最终 redirect URL; 若顶层 + JSONB 都没填或 promotion.is_active=false 返 None.

    URL 来源优先级 (Sprint 11 BE-S11-A04 双字段):
    1. ``broker.open_account_url`` (顶层, admin 编辑入口, 长期稳定)
    2. ``broker.promotion.referral_url`` (JSONB fallback, 兼容旧 seed; 但需要
       ``promotion.is_active=True``, 这跟 "开户" 长期可用 vs "活动" 季节性的语义对应)

    顶层 URL 跳转**不受 promotion.is_active 控制** — admin 维护了开户地址就一直能跳;
    只有完全没维护顶层 + JSONB 也关了活动, 才返 None (404).

    URL 拼接走 ``urlparse + urlencode + urlunparse``, 不用 f-string —
    防 utm_campaign='&evil=1' 注入既有 query 参数; 同时保留 referral_url
    自带的 utm_source 不被覆盖 (参数级 merge: 我方 utm_* 仅在 broker 没设时填).
    """
    promo = broker.promotion or {}
    # Sprint 11: 优先用顶层 open_account_url; 否则走 promotion.referral_url (需 is_active)
    base_url = broker.open_account_url
    if not base_url:
        if not promo.get("is_active"):
            return None
        base_url = promo.get("referral_url")
    if not base_url or not isinstance(base_url, str):
        return None

    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        logger.warning(
            f"conversion.build_url.invalid_referral broker={broker.slug} url={base_url}"
        )
        return None

    existing_pairs: list[tuple[str, str]] = []
    if parsed.query:
        for kv in parsed.query.split("&"):
            if not kv:
                continue
            if "=" in kv:
                k, v = kv.split("=", 1)
            else:
                k, v = kv, ""
            existing_pairs.append((k, v))
    existing_keys = {k for k, _ in existing_pairs}

    extra: dict[str, str] = {}
    if "utm_source" not in existing_keys:
        extra["utm_source"] = "xgzh"
    if utm_campaign and "utm_campaign" not in existing_keys:
        extra["utm_campaign"] = utm_campaign
    if utm_medium and "utm_medium" not in existing_keys:
        extra["utm_medium"] = utm_medium

    invite = promo.get("invite_code")
    if isinstance(invite, str) and invite and "invite_code" not in existing_keys:
        extra["invite_code"] = invite

    encoded_extra = urlencode(extra) if extra else ""
    parts = [
        kv
        for kv in (
            urlencode(existing_pairs) if existing_pairs else "",
            encoded_extra,
        )
        if kv
    ]
    final_query = "&".join(parts)

    final_url: str = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            final_query,
            parsed.fragment,
        )
    )
    return final_url


# ─── 3. 30d stats ─────────────────────────────────────────────────────────


async def get_broker_stats_30d(
    *,
    broker: Broker,
    window_days: int = STATS_DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    """GROUP BY event_type 统计 30d 转化漏斗 + 累计核销入金.

    走 ``ix_conversion_events_broker_event_created`` 索引 (broker_id, event_type,
    created_at DESC) — 命中范围扫描, 单 broker < 50ms.

    Returns:
        ``{slug, broker_id, window_days, clicks, signups, kyc_pass, deposits,
        first_trades, total_amount_cny}`` — 路由层 ``BrokerStats30d.model_validate``
        重构成 schema.
    """
    cutoff = datetime.now(UTC) - timedelta(days=window_days)

    counts_stmt = (
        select(ConversionEvent.event_type, func.count())
        .where(
            ConversionEvent.broker_id == broker.broker_id,
            ConversionEvent.created_at >= cutoff,
        )
        .group_by(ConversionEvent.event_type)
    )
    sum_stmt = select(
        func.coalesce(func.sum(ConversionEvent.amount_cny), 0)
    ).where(
        ConversionEvent.broker_id == broker.broker_id,
        ConversionEvent.created_at >= cutoff,
        ConversionEvent.attributed.is_(True),
    )

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(counts_stmt)).all()
        total_amount = (await session.execute(sum_stmt)).scalar_one()

    counts: dict[str, int] = dict.fromkeys(
        ("click", "signup", "kyc_pass", "deposit", "first_trade"), 0
    )
    for et, c in rows:
        if et in counts:
            counts[et] = int(c)

    return {
        "slug": broker.slug,
        "broker_id": str(broker.broker_id),
        "window_days": window_days,
        "clicks": counts["click"],
        "signups": counts["signup"],
        "kyc_pass": counts["kyc_pass"],
        "deposits": counts["deposit"],
        "first_trades": counts["first_trade"],
        "total_amount_cny": float(total_amount or 0),
    }


# ─── 4. helper: 拿 active broker by slug ─────────────────────────────────


async def get_active_broker_by_slug(slug: str) -> Broker | None:
    """端点用: ``WHERE slug=? AND is_active=TRUE AND deleted_at IS NULL`` 拿 broker.

    与 ``broker_service.get_broker_detail`` 区别: 那个走 cache, 返 dict;
    这里返 ORM 实例 (服务下游 ``log_click_with_dedup`` / ``build_redirect_url``
    都直接拿 ORM 字段, 不走 cache 路径 — 跳转 / 落库实时性 > 缓存).
    """
    if not slug or not slug.strip():
        return None
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(Broker).where(
                    Broker.slug == slug.strip(),
                    Broker.is_active.is_(True),
                    Broker.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
