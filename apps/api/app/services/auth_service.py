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
from app.services import otp_service, user_service
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
    """新建 phone 用户 + 生成唯一 invite_code (冲突重试)."""
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
    "refresh_tokens",
    "revoke_access_token",
    "revoke_refresh_token",
    "verify_phone_login",
]
