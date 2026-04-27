"""券商相关路由 (BE-S3-007 横向对比 API + BE-S3-008 跳转 / 转化埋点).

- ``GET /brokers``: 列表 + 3 维筛选 (market_support / partnership_type / only_active)
- ``GET /brokers/{slug}``: 详情 by slug (URL 友好, ``/brokers/futubull``)
- ``GET /brokers/{slug}/redirect``: 落 ``conversion_events`` (event_type=click) +
  302 到券商 referral_url (带 utm 参数) [BE-S3-008]
- ``GET /brokers/{slug}/stats``: 30d 转化漏斗统计 (auth required) [BE-S3-008]
- ``POST /brokers/postback``: 券商 Postback 占位 → 501 (Sprint 4+ 接) [BE-S3-008]

partnership_* 隔离
==================
service 层返完整 dict (含 partnership_* 三字段), 路由层用 ``to_public_dict``
显式剥掉再 ``BrokerPublic.model_validate``; ``BrokerPublic`` ``extra="forbid"``
做防御 in depth (即便忘记调 helper, 也会 raise 而非偷偷泄漏).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.db.models import User
from app.schemas.broker import (
    BrokerListResponse,
    BrokerPublic,
    to_public_dict,
)
from app.schemas.conversion import BrokerStats30d, PostbackRequest
from app.security.deps import get_current_user, get_optional_user
from app.services import broker_service, conversion_service

router = APIRouter(prefix="/brokers", tags=["brokers"])


def _resolve_client_ip(request: Request) -> str | None:
    """优先 ``X-Forwarded-For`` 第一段; fallback ``request.client.host``.

    与 ``app/api/v1/chat.py::_resolve_client_ip`` 同款语义 (那个是 chat 模块私有,
    这里复刻一份避免循环依赖); 单测 ASGI Transport 返 ``"testclient"``.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client is not None:
        return request.client.host
    return None


def _resolve_actor_key(
    *, user: User | None, device_id: str | None, ip: str | None
) -> str | None:
    """防刷 dedup key 的 actor 部分: user_id > device_id > ip.

    user_id 优先级最高 (登录用户跨设备点击应去重); 其次 device_id (匿名用户多设备);
    最后退化 IP (匿名 + 没 device_id; 共享 IP 场景会误杀, 但比"完全不防刷"好).
    全 None 极端情况由 ``log_click_with_dedup`` 走"不防刷"分支兜底.
    """
    if user is not None:
        return f"u:{user.user_id}"
    if device_id and device_id.strip():
        return f"d:{device_id.strip()}"
    if ip:
        return f"ip:{ip}"
    return None


@router.get(
    "",
    response_model=BrokerListResponse,
    summary="券商列表 (3 维筛选 + display_order DESC 排序)",
)
async def list_brokers(
    market: Annotated[
        broker_service.MarketFilter,
        Query(description="支持市场: HK / A / US / SG / all (默认 all)"),
    ] = "all",
    partnership: Annotated[
        broker_service.PartnershipFilter,
        Query(
            description=(
                "合作类型: CPA / CPS / BOTH / NONE / all; "
                "FE 通常用 all (展示所有券商); 内部运营路由可走 BOTH"
            )
        ),
    ] = "all",
) -> BrokerListResponse:
    """券商列表. 默认隐藏 ``is_active=False`` 的券商 (运营临时下架)."""
    payload = await broker_service.list_brokers(
        market=market, partnership=partnership, only_active=True
    )
    items_public = [
        BrokerPublic.model_validate(to_public_dict(item)) for item in payload["items"]
    ]
    return BrokerListResponse(items=items_public, total=int(payload["total"]))


@router.get(
    "/{slug}",
    response_model=BrokerPublic,
    summary="券商详情 by slug",
    responses={404: {"description": "slug 不存在或券商已下架"}},
)
async def get_broker_detail(slug: str) -> BrokerPublic:
    payload = await broker_service.get_broker_detail(slug)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "broker_not_found",
                "message": f"broker {slug} not found",
            },
        )
    return BrokerPublic.model_validate(to_public_dict(payload))


# ─── BE-S3-008: redirect / stats / postback ────────────────────────────────


@router.get(
    "/{slug}/redirect",
    summary="跳转到券商开户页 (落 conversion_events click + 302)",
    responses={
        302: {"description": "302 重定向到券商带 UTM 的 referral_url"},
        404: {"description": "slug 不存在 / 券商已下架 / 当前 promotion 未启用"},
    },
)
async def redirect_to_broker(
    slug: str,
    request: Request,
    user: Annotated[User | None, Depends(get_optional_user)],
    utm_campaign: Annotated[
        str | None,
        Query(max_length=64, description="活动归因 ID, 透传到券商 referral URL"),
    ] = None,
    utm_medium: Annotated[
        str | None, Query(max_length=32, description="渠道, 如 ipo-detail / compare-page")
    ] = None,
    device_id: Annotated[
        str | None,
        Query(
            max_length=64,
            description="前端拦截器自动注入 (与 push_tokens.device_id 同语义); 匿名防刷 key",
        ),
    ] = None,
) -> RedirectResponse:
    """匿名 + 登录两态都通; 1h 防刷; UX 优先 (即便不落库也 302).

    流程:
    1. 取活跃 broker (slug + is_active=True + deleted_at IS NULL); 失败 → 404
    2. 拼 redirect_url (urlencode 防注入); promotion.is_active=False 或缺 referral_url → 404
    3. 落 ``conversion_events`` (event_type='click'); 防刷命中则跳过, 不阻塞 302
    4. 返 ``RedirectResponse(status_code=302, ...)``
    """
    broker = await conversion_service.get_active_broker_by_slug(slug)
    if broker is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "broker_not_found",
                "message": f"broker {slug} not found",
            },
        )

    redirect_url = conversion_service.build_redirect_url(
        broker, utm_campaign=utm_campaign, utm_medium=utm_medium
    )
    if redirect_url is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "broker_promotion_inactive",
                "message": f"broker {slug} 未启用推广 / 缺少 referral_url",
            },
        )

    ip = _resolve_client_ip(request)
    user_agent = request.headers.get("user-agent")
    referer = request.headers.get("referer")
    actor_key = _resolve_actor_key(user=user, device_id=device_id, ip=ip)
    effective_device = (
        (device_id or "").strip() or (f"anon-ip:{ip}" if ip else "anon")
    )

    await conversion_service.log_click_with_dedup(
        broker=broker,
        actor_key=actor_key,
        user_id=user.user_id if user is not None else None,
        device_id=effective_device,
        utm_campaign=utm_campaign,
        utm_medium=utm_medium,
        referer=referer,
        ip_addr=ip,
        user_agent=user_agent,
    )

    return RedirectResponse(url=redirect_url, status_code=302)


@router.get(
    "/{slug}/stats",
    response_model=BrokerStats30d,
    summary="券商 30d 转化漏斗 (clicks / signups / kyc / deposit / first_trade)",
    responses={
        401: {"description": "未登录"},
        404: {"description": "slug 不存在 / 券商已下架"},
    },
)
async def get_broker_stats(
    slug: str,
    _: Annotated[User, Depends(get_current_user)],
    window_days: Annotated[
        int, Query(ge=1, le=365, description="统计窗口 (天)")
    ] = 30,
) -> BrokerStats30d:
    """30d 转化漏斗 (auth-only).

    spec 写"仅运营 / VIP", BE-S3-009 上线 VIP 闸门后这里再加 ``Depends(require_vip)``;
    本 PR 暂仅做 auth 级别拦截, 防匿名爬刷.
    """
    broker = await conversion_service.get_active_broker_by_slug(slug)
    if broker is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "broker_not_found",
                "message": f"broker {slug} not found",
            },
        )

    payload = await conversion_service.get_broker_stats_30d(
        broker=broker, window_days=window_days
    )
    return BrokerStats30d.model_validate(payload)


@router.post(
    "/postback",
    summary="[Sprint 4+] 券商 Postback 接收 (本 PR 占位 → 501)",
    responses={
        501: {"description": "Sprint 4+ 才实装; 当前签名校验 + 入库逻辑未完成"},
    },
    status_code=501,
)
async def postback_placeholder(_: PostbackRequest) -> dict[str, str]:
    """Sprint 4+ 才实装的 Postback 占位.

    保留路由 + schema 是为了让券商 BD 提前对接调试 (能拿到 URL + 字段定义),
    Sprint 4+ 接入时只需把这里改成 ``201 + insert ConversionEvent`` 即可,
    URL / 字段契约不变.
    """
    raise HTTPException(
        status_code=501,
        detail={
            "code": "postback_not_implemented",
            "message": (
                "券商 Postback 接收暂未实装 (Sprint 4+); 字段契约见 PostbackRequest schema"
            ),
        },
    )
