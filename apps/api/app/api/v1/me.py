"""当前用户路由 (BE-003 / BE-S5-003 / BUG-S6.8-002).

Sprint 1: ``GET /me``
Sprint 5 BE-S5-003: ``DELETE /me`` (PIPL §47 注销账号)
Sprint 6.8 BUG-S6.8-002: ``PATCH /me`` (昵称编辑)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.schemas.auth import UserPublic
from app.schemas.me import DeleteMeRequest, DeleteMeResponse, UpdateMeRequest
from app.security import (
    ACCESS_TOKEN_TYPE,
    AccessTokenPayload,
    InvalidTokenError,
    TokenExpiredError,
    decode_token,
    get_current_user,
)
from app.services import user_deletion_service
from app.services.user_deletion_service import UserAlreadyDeletedError

router = APIRouter(prefix="/me", tags=["me"])


# ─── GET /me (BE-003) ─────────────────────────────────────────────


@router.get(
    "",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
    summary="当前用户基本信息",
    responses={401: {"description": "未登录 / token 无效 / token 过期"}},
)
async def read_me(current_user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.model_validate(current_user)


# ─── PATCH /me (BUG-S6.8-002 资料编辑) ────────────────────────────


@router.patch(
    "",
    response_model=UserPublic,
    status_code=status.HTTP_200_OK,
    summary="编辑当前用户资料 (Sprint 6.8 起仅支持昵称)",
    responses={
        400: {"description": "字段校验失败 (空昵称 / 超长 / 全空白)"},
        401: {"description": "未登录 / token 无效 / token 过期"},
    },
)
async def update_me(
    body: UpdateMeRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserPublic:
    """编辑当前用户基本资料.

    BUG-S6.8-002 范围: 仅 ``nickname``. 后续扩展 (avatar_url / region) 直接
    在 ``UpdateMeRequest`` 加字段即可, 服务层逻辑同款 (优先校验, 写库, 返
    refreshed user).

    业务规则:
    - 昵称去首尾空白后必须 1-20 字 (中英文混算)
    - 不传字段或传 None 视为不改 — 用 ``exclude_unset`` 拿到非空 patch
    - 字段全空 → 400 不动 (避免无意义请求)
    """
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "no_change",
                "message": "请求未指定要修改的字段",
            },
        )

    if "nickname" in patch:
        new_nickname = (patch["nickname"] or "").strip()
        if not new_nickname:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "nickname_empty",
                    "message": "昵称不能为空",
                },
            )
        if len(new_nickname) > 20:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "nickname_too_long",
                    "message": "昵称最长 20 字",
                },
            )
        current_user.nickname = new_nickname

    await session.flush()
    await session.refresh(current_user)
    logger.info(
        f"me.update.ok user_id={current_user.user_id} fields={list(patch.keys())}"
    )
    return UserPublic.model_validate(current_user)


# ─── DELETE /me (BE-S5-003 PIPL §47 注销账号) ─────────────────────


def _extract_bearer_token(request: Request) -> str | None:
    """从 ``Authorization: Bearer xxx`` 抓 token; ``get_current_user`` 已校验过 header 合法,
    这里复用 (与 auth.py logout 同款实现).
    """
    raw = request.headers.get("authorization")
    if not raw:
        return None
    parts = raw.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _resolve_client_ip(request: Request) -> str | None:
    """从 X-Forwarded-For (取首个) 或 request.client.host 抓 IP.

    与 ``feedback_service`` 同款; 反代后 X-Forwarded-For 是真实 IP, 直接 client.host
    会拿到反代 IP. 信任 header 是因为反代会重写, 没反代的 dev 直接用 client.host.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client is not None:
        return request.client.host
    return None


@router.delete(
    "",
    response_model=DeleteMeResponse,
    status_code=status.HTTP_200_OK,
    summary="注销账号 (PIPL §47): 软删 + 30d 后真删 PII",
    responses={
        401: {"description": "未登录 / token 无效 / token 过期"},
        409: {"description": "用户已注销过 (重复请求)"},
    },
)
async def delete_me(
    request: Request,
    body: DeleteMeRequest | None = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DeleteMeResponse:
    """注销账号: 标 soft delete + audit + 拉黑当前 access + 吊销所有 refresh.

    流程:
    1. ``soft_delete_user`` 在同事务里完成所有 DB 改动 (users / auth_sessions /
       invite_codes / user_deletions audit) + Redis 黑名单
    2. 实际 PII 真删由 30d 后 cron (``user_deletion_service.run_hard_delete_pii_job``) 跑
    3. 客户端必须本地清 token, 跳转登录页 — 后续请求 ``get_current_user`` 会因
       ``status=0`` 或 ``token_revoked`` 401 拒绝

    PIPL 合规要点:
    - ``deleted_at`` 标在 user row, 不立即清字段 — 让 30d 内能反悔 (但本 PR 没做"撤回",
      留 5.5+ 加 ``POST /me/restore``; 现阶段误注销只能联系客服)
    - 30d 后 cron 真删 phone / wechat_* / apple_id / nickname / avatar_url
      所有 PII 字段, 保留 user_id / region / 时间戳 (财务 / 渠道审计要)
    - 反馈 / VIP 订单 / conversion_events 不删 (财务监管 7 年留存; 已无 PII)
    """
    body = body or DeleteMeRequest()

    # 拆 access payload (用来拉黑 jti). 拿不到不阻断主路径 — 极端容忍 (反正 user.status=0
    # 已经能让所有后续请求 401)
    raw_token = _extract_bearer_token(request)
    access_payload: AccessTokenPayload | None = None
    if raw_token:
        try:
            decoded = decode_token(raw_token, expected_type=ACCESS_TOKEN_TYPE)
        except (InvalidTokenError, TokenExpiredError):
            decoded = None
        if isinstance(decoded, AccessTokenPayload):
            access_payload = decoded

    if access_payload is None:
        # 不该发生 (get_current_user 已经走过同款 decode), 但兜底; 没法拉黑 jti 时
        # 至少把 user.status=0 写下来, 让其它请求失败
        logger.warning(
            f"me.delete.no_payload user_id={current_user.user_id} "
            "(get_current_user 通过但 payload 解析失败?)"
        )

    ip = _resolve_client_ip(request)
    user_agent = request.headers.get("user-agent")

    try:
        result = await user_deletion_service.soft_delete_user(
            session,
            user=current_user,
            access_payload=access_payload,  # type: ignore[arg-type]
            reason=body.reason,
            ip=ip,
            user_agent=user_agent,
        )
    except UserAlreadyDeletedError as e:
        # 极端: get_current_user 已检查 status=1, 但并发场景理论可能撞 (两个请求同时调)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "user_already_deleted",
                "message": "账号已经注销, 请勿重复请求",
            },
        ) from e

    # 路由层 get_session 在正常返回时 commit; 异常自动 rollback (不需要手动)

    return DeleteMeResponse(
        deleted=True,
        user_id=result.user_id,
        deleted_at=result.deleted_at,
        real_purge_scheduled_at=result.real_purge_scheduled_at,
        audit_id=result.audit_id,
    )
