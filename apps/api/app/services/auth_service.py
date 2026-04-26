"""鉴权业务编排 (BE-002).

职责:
1. 校验 OTP (常量时间比较, 一次性消费)
2. 用户不存在 -> 自动注册 (生成 invite_code, 写 ``users`` 表)
3. 颁发 access + refresh JWT 双 token
4. 更新 ``users.last_active_at``

不放进路由的原因: 单测可在不起 FastAPI 的前提下覆盖完整业务流。
"""

from __future__ import annotations

import hmac
import secrets
import string
import uuid
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import User
from app.security import (
    REFRESH_TOKEN_TYPE,
    AccessTokenPayload,
    InvalidTokenError,
    RefreshTokenPayload,
    TokenExpiredError,
    blacklist_jti,
    create_access_token,
    create_refresh_token,
    decode_token,
    is_jti_blacklisted,
)
from app.services import invite_service, otp_service, user_service
from app.utils.phone import mask_phone

INVITE_CODE_ALPHABET = string.ascii_uppercase + string.digits  # 去歧义留待 BE-006 优化
INVITE_CODE_LENGTH = 8
INVITE_CODE_RETRY = 5


class OTPNotFoundError(Exception):
    """Redis 中无该手机号 OTP. 前端语义: 未发送 / 已过期 / 已被消费."""


class OTPInvalidError(Exception):
    """OTP 不匹配."""


class RefreshTokenError(Exception):
    """Refresh 失败的统一基类. 子类区分 reason, 路由层映射成不同 401 detail.code."""


class RefreshTokenInvalid(RefreshTokenError):
    """签名错 / 缺 claim / typ 不是 refresh / sub 解不出。"""


class RefreshTokenExpired(RefreshTokenError):
    """超过 30d TTL, 自然过期, 客户端必须重新登录。"""


class RefreshTokenRevoked(RefreshTokenError):
    """jti 已在黑名单 (logout 过 / 被 rotation 替换 / 被风控)。"""


class RefreshUserUnavailable(RefreshTokenError):
    """sub 对应用户不存在 / 被禁用。token 合法但用户已不可用。"""


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    access_expires_in: int
    refresh_expires_in: int


def _generate_invite_code() -> str:
    return "".join(secrets.choice(INVITE_CODE_ALPHABET) for _ in range(INVITE_CODE_LENGTH))


async def _create_user_with_phone(session: AsyncSession, phone: str) -> User:
    """新建 phone 用户 + 生成唯一 invite_code (冲突重试) + 同事务镜像到 ``invite_codes`` (BE-006)."""
    last_err: Exception | None = None
    for _ in range(INVITE_CODE_RETRY):
        invite_code = _generate_invite_code()
        user = User(phone=phone, invite_code=invite_code)
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as e:
            await session.rollback()
            last_err = e
            # 区分是 phone 冲突(说明并发注册) 还是 invite_code 冲突(继续重试)
            if "uq_users_phone" in str(e.orig).lower():
                # 并发场景: 另一请求刚注册完, 直接 fetch
                existing = await user_service.find_user_by_phone(session, phone)
                if existing is not None:
                    return existing
                raise
            continue
        # BE-006: user.invite_code 同事务镜像到 invite_codes 表 (PK = code, 同样唯一)
        await invite_service.register_invite_code_for_user(session, user)
        await session.refresh(user)
        return user

    assert last_err is not None
    raise RuntimeError(
        f"failed to allocate unique invite_code after {INVITE_CODE_RETRY} retries"
    ) from last_err


async def _create_user_with_wechat(
    session: AsyncSession, *, openid: str, unionid: str | None
) -> User:
    """新建微信用户 + 生成唯一 invite_code (冲突重试).

    并发处理: openid 唯一约束 (``uq_users_wechat_openid``) 撞了说明另一请求刚注册完,
    fetch 一次返回。unionid 没有 unique 约束 (允许多账号同 unionid? 否, 业务上一个 unionid
    只对一个用户; 不过是否唯一约束属于业务策略, INFRA-001 里只加了索引, 这里不强制)。
    """
    last_err: Exception | None = None
    for _ in range(INVITE_CODE_RETRY):
        invite_code = _generate_invite_code()
        user = User(
            wechat_openid=openid,
            wechat_unionid=unionid,
            invite_code=invite_code,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as e:
            await session.rollback()
            last_err = e
            origin = str(e.orig).lower()
            if "uq_users_wechat_openid" in origin:
                existing = await user_service.find_user_by_wechat_openid(session, openid)
                if existing is not None:
                    return existing
                raise
            # invite_code 冲突, 重试
            continue
        # BE-006: 镜像到 invite_codes 表
        await invite_service.register_invite_code_for_user(session, user)
        await session.refresh(user)
        return user

    assert last_err is not None
    raise RuntimeError(
        f"failed to allocate unique invite_code after {INVITE_CODE_RETRY} retries"
    ) from last_err


async def find_or_create_user_by_phone(
    session: AsyncSession, phone: str
) -> tuple[User, bool]:
    """返回 ``(user, is_new)``."""
    user = await user_service.find_user_by_phone(session, phone)
    if user is not None:
        return user, False
    user = await _create_user_with_phone(session, phone)
    return user, True


async def _touch_last_active(session: AsyncSession, user_id: uuid.UUID) -> None:
    stmt = (
        update(User)
        .where(User.user_id == user_id)
        .values(last_active_at=func.now())
    )
    await session.execute(stmt)


async def verify_phone_login(
    session: AsyncSession,
    *,
    phone: str,
    code: str,
    settings: Settings | None = None,
) -> tuple[User, bool, IssuedTokens]:
    """OTP 校验 + 注册 + 颁发 token. ``phone`` 必须已经 normalize_phone 过.

    Raises:
        OTPNotFoundError: Redis 中无该 OTP (未发送 / 过期 / 已消费)
        OTPInvalidError: OTP 不匹配
    """
    settings = settings or get_settings()

    stored = await otp_service.fetch_stored_otp(phone)
    if stored is None:
        logger.info(f"otp.verify.miss phone={mask_phone(phone)}")
        raise OTPNotFoundError("otp not found or expired")

    # 常量时间比较, 防止侧信道
    if not hmac.compare_digest(stored.encode("utf-8"), code.encode("utf-8")):
        logger.info(f"otp.verify.mismatch phone={mask_phone(phone)}")
        raise OTPInvalidError("otp mismatch")

    # 一次性消费 (无论后续注册成功失败, OTP 都失效, 防 race + 防重放)
    await otp_service.consume_otp(phone)

    user, is_new = await find_or_create_user_by_phone(session, phone)
    await _touch_last_active(session, user.user_id)
    await session.commit()

    access_token, access_payload = create_access_token(user.user_id, settings)
    refresh_token, refresh_payload = create_refresh_token(user.user_id, settings)

    logger.info(
        f"auth.login.ok user_id={user.user_id} phone={mask_phone(phone)} new={is_new}"
    )

    return (
        user,
        is_new,
        IssuedTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_in=settings.jwt_access_ttl_seconds,
            refresh_expires_in=settings.jwt_refresh_ttl_seconds,
        ),
    )


USER_STATUS_ACTIVE = 1


async def find_or_create_user_by_wechat(
    session: AsyncSession,
    *,
    openid: str,
    unionid: str | None,
) -> tuple[User, bool]:
    """微信登录的"找用户"入口. 返回 ``(user, is_new)``。

    匹配优先级:
        1. ``unionid`` (若有) — 跨小程序/公众号稳定; 命中时顺手把 openid 同步进 user
        2. ``openid`` — 当前小程序内稳定的 fallback; 命中时顺手把 unionid 补上 (如有)
        3. 都未命中 → 创建新用户
    """
    user: User | None = None
    if unionid:
        user = await user_service.find_user_by_wechat_unionid(session, unionid)
    if user is None:
        user = await user_service.find_user_by_wechat_openid(session, openid)

    if user is not None:
        # 同步可能缺失的字段; 跨小程序场景 openid 可能换, 但 unionid 一致, 我们以 unionid 优先,
        # openid 字段始终覆盖为本次登录拿到的最新 openid (代表"最近一次登录的小程序")。
        changed = False
        if user.wechat_openid != openid:
            user.wechat_openid = openid
            changed = True
        if unionid and user.wechat_unionid != unionid:
            user.wechat_unionid = unionid
            changed = True
        if changed:
            try:
                await session.flush()
            except IntegrityError as e:
                # openid 唯一索引冲突: 这个 openid 已经绑在另一用户上 (跨账号迁移?),
                # 不在 MVP 范围, 报错回路由层 502/409 让人工介入
                await session.rollback()
                raise RuntimeError(
                    f"wechat openid {openid!r} conflicts with another user"
                ) from e
        return user, False

    user = await _create_user_with_wechat(session, openid=openid, unionid=unionid)
    return user, True


async def verify_wechat_mp_login(
    session: AsyncSession,
    *,
    openid: str,
    unionid: str | None,
    settings: Settings | None = None,
) -> tuple[User, bool, IssuedTokens]:
    """已经从微信侧拿到 (openid, unionid) 后的本地业务流: 找/建用户 + 颁发 token。

    与 ``verify_phone_login`` 的区别仅在"凭据从哪来", 后续步骤完全复用。
    """
    settings = settings or get_settings()

    user, is_new = await find_or_create_user_by_wechat(
        session, openid=openid, unionid=unionid
    )
    if user.status != USER_STATUS_ACTIVE:
        # 老账号被风控/封禁, 不能借微信登录绕过
        logger.warning(
            f"auth.wechat.disabled user_id={user.user_id} status={user.status}"
        )
        raise RefreshUserUnavailable("user disabled")

    await _touch_last_active(session, user.user_id)
    await session.commit()

    tokens = _issue_token_pair(user.user_id, settings)
    logger.info(
        f"auth.wechat.ok user_id={user.user_id} new={is_new} "
        f"has_unionid={unionid is not None}"
    )
    return user, is_new, tokens


def _issue_token_pair(user_id: uuid.UUID, settings: Settings) -> IssuedTokens:
    """统一颁发 access+refresh, 路由层不需要再关心 ttl 字段."""
    access_token, _ = create_access_token(user_id, settings)
    refresh_token, _ = create_refresh_token(user_id, settings)
    return IssuedTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_in=settings.jwt_access_ttl_seconds,
        refresh_expires_in=settings.jwt_refresh_ttl_seconds,
    )


async def refresh_tokens(
    session: AsyncSession,
    *,
    refresh_token: str,
    settings: Settings | None = None,
) -> tuple[User, IssuedTokens]:
    """换发新 token (refresh token rotation).

    安全约束:
    1. 严格按 ``typ=refresh`` 解码, access token 当 refresh 用直接 401
    2. jti 命中黑名单 → 拒绝 (防止旧 refresh 被偷或被 rotate 后再用)
    3. 解出的 ``sub`` 用户必须存在 / 未软删 / status=1
    4. **rotation**: 颁发新 refresh 前, 把旧 refresh 的 jti 拉黑 (TTL=旧 refresh 剩余有效期).
       这样即便旧 refresh 被中间人复制, 第二次也用不了 (一次性凭据)。
    5. access 不需要拉黑: 新 access 有自己的 jti, 旧 access 自然过期就行 (反正最长 30min)。
       ⚠️ 例外: 安全场景需要"刷新即踢旧设备"时, 调用方可以另行调 ``logout``。

    Raises:
        RefreshTokenInvalid / RefreshTokenExpired / RefreshTokenRevoked /
        RefreshUserUnavailable
    """
    settings = settings or get_settings()
    try:
        payload = decode_token(refresh_token, expected_type=REFRESH_TOKEN_TYPE, settings=settings)
    except TokenExpiredError as e:
        raise RefreshTokenExpired(str(e)) from e
    except InvalidTokenError as e:
        raise RefreshTokenInvalid(str(e)) from e

    assert isinstance(payload, RefreshTokenPayload)

    if await is_jti_blacklisted(payload.jti):
        logger.info(f"auth.refresh.revoked jti={payload.jti} user_id={payload.user_id}")
        raise RefreshTokenRevoked("refresh token revoked")

    user = await user_service.find_user_by_id(session, payload.user_id)
    if user is None or user.status != USER_STATUS_ACTIVE:
        logger.info(
            f"auth.refresh.user_unavailable user_id={payload.user_id} "
            f"exists={user is not None} status={getattr(user, 'status', None)}"
        )
        raise RefreshUserUnavailable("user not found or disabled")

    # rotation: 先拉黑旧 refresh, 再发新 token. 顺序不可换 (新发完再拉黑会有窗口期).
    await blacklist_jti(payload.jti, payload.expires_at, reason="refresh-rotate")

    await _touch_last_active(session, user.user_id)
    await session.commit()

    tokens = _issue_token_pair(user.user_id, settings)
    logger.info(f"auth.refresh.ok user_id={user.user_id} old_jti={payload.jti}")
    return user, tokens


async def revoke_access_token(payload: AccessTokenPayload, *, reason: str = "logout") -> bool:
    """把当前 access token 加入黑名单 (logout 用). 返回 ``True`` 表示已写入黑名单。"""
    return await blacklist_jti(payload.jti, payload.expires_at, reason=reason)


async def revoke_refresh_token(
    refresh_token: str,
    *,
    expected_user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    reason: str = "logout",
) -> bool:
    """把传入的 refresh_token 加入黑名单。失败返回 ``False`` 而不抛, 因为 logout 是
    "尽力而为"语义: 即便 refresh 已经无效, access 拉黑了也算成功登出。

    若 ``expected_user_id`` 指定且与 token sub 不符 (恶意 logout 别人), 直接拒绝。
    """
    settings = settings or get_settings()
    try:
        payload = decode_token(
            refresh_token, expected_type=REFRESH_TOKEN_TYPE, settings=settings
        )
    except TokenExpiredError:
        logger.debug("logout.refresh.expired - skip blacklist")
        return False
    except InvalidTokenError as e:
        logger.info(f"logout.refresh.invalid - skip blacklist: {e}")
        return False

    assert isinstance(payload, RefreshTokenPayload)
    if expected_user_id is not None and payload.user_id != expected_user_id:
        logger.warning(
            f"logout.refresh.user_mismatch token_sub={payload.user_id} "
            f"expected={expected_user_id}; refusing to blacklist"
        )
        return False
    return await blacklist_jti(payload.jti, payload.expires_at, reason=reason)


__all__ = [
    "INVITE_CODE_LENGTH",
    "IssuedTokens",
    "OTPInvalidError",
    "OTPNotFoundError",
    "RefreshTokenError",
    "RefreshTokenExpired",
    "RefreshTokenInvalid",
    "RefreshTokenRevoked",
    "RefreshUserUnavailable",
    "find_or_create_user_by_phone",
    "find_or_create_user_by_wechat",
    "refresh_tokens",
    "revoke_access_token",
    "revoke_refresh_token",
    "verify_phone_login",
    "verify_wechat_mp_login",
]
