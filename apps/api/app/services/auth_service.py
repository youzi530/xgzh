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
from app.services import invite_service, otp_service, user_service, vip_service
from app.services.security_password import (
    PasswordTooLongError,
    hash_password,
    verify_password,
)
from app.utils.email import (
    InvalidEmailError,
    looks_like_email,
    mask_email,
    normalize_email,
)
from app.utils.phone import InvalidPhoneError, mask_phone, normalize_phone

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


# ----------------------------- BUG-S9-001 密码登录异常 -----------------------------


class PasswordAuthError(Exception):
    """密码鉴权相关业务异常基类."""


class PhoneAlreadyExistsError(PasswordAuthError):
    """注册时 phone 已被其它账号占用 (unique 撞 → 应该让用户去登录)."""


class EmailAlreadyExistsError(PasswordAuthError):
    """注册时 email 已被其它账号占用."""


class InvalidCredentialsError(PasswordAuthError):
    """密码错 / 用户不存在 — **统一**抛这个异常防 enumeration attack.

    永远不让攻击者通过观察异常类型来推断"这个 phone/email 是不是真存在".
    路由层映射 401 ``invalid_credentials``.
    """


class PasswordNotSetError(PasswordAuthError):
    """老 OTP 用户 / 微信用户用密码登录 — DB 里 password_hash IS NULL.

    与 InvalidCredentialsError 区分: 这是**已知用户**, 但他还没设过密码,
    应该提示"用 OTP / 微信登录后去设置密码", 而不是泄露存在性. 但实际
    路由层仍然映射成 401 ``invalid_credentials`` 防 enum.
    内部用这个区分主要是给 logger / 监控用.
    """


class CurrentPasswordInvalidError(PasswordAuthError):
    """改密时旧密码错. 与 InvalidCredentialsError 区分: 这是**已登录**用户,
    我们已经知道他是谁, 错误就是字面错, 路由层映射 401 ``current_password_invalid``."""


class IdentifierFormatError(PasswordAuthError):
    """登录 / 注册时 identifier 格式错 (既不是合法 phone 也不是合法 email)."""


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
        # BE-S3-009: 注册成功同事务赠 7d VIP 试用 (零元订单 + trialing membership)
        # 失败兜底走 try: 试用授予不应阻塞注册主路径 (用户无 VIP 也能用免费档)
        try:
            await vip_service.grant_trial(session, user)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"vip.grant_trial.fail_phone user_id={user.user_id} err={e!r}"
            )
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
        # BE-S3-009: 微信注册同样赠 7d VIP 试用; 失败兜底, 不阻塞注册
        try:
            await vip_service.grant_trial(session, user)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"vip.grant_trial.fail_wechat user_id={user.user_id} err={e!r}"
            )
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


# ----------------------------- BUG-S9-001 密码注册 / 登录 / 设密码 -----------------------------


async def _create_user_with_password(
    session: AsyncSession,
    *,
    phone: str | None,
    email: str | None,
    password_hash: str,
) -> User:
    """新建密码用户 + 生成唯一 invite_code (冲突重试) + 同事务镜像 invite_codes (BE-006).

    与 ``_create_user_with_phone`` 区别: 同事务把 password_hash 落 DB, 并支持
    ``email`` (phone 单值或 phone+email 双值都行). 并发场景的 unique 撞:
    - phone 撞 → ``PhoneAlreadyExistsError``
    - email 撞 → ``EmailAlreadyExistsError``
    """
    last_err: Exception | None = None
    for _ in range(INVITE_CODE_RETRY):
        invite_code = _generate_invite_code()
        user = User(
            phone=phone,
            email=email,
            password_hash=password_hash,
            invite_code=invite_code,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as e:
            await session.rollback()
            last_err = e
            origin = str(e.orig).lower()
            if "uq_users_phone" in origin:
                raise PhoneAlreadyExistsError(
                    f"phone {mask_phone(phone) if phone else '?'} already registered"
                ) from e
            if "uq_users_email" in origin:
                raise EmailAlreadyExistsError(
                    f"email {mask_email(email) if email else '?'} already registered"
                ) from e
            # invite_code 冲突, 重试
            continue
        # BE-006 + BE-S3-009: 同事务镜像 invite_codes + 赠 7d VIP 试用
        await invite_service.register_invite_code_for_user(session, user)
        try:
            await vip_service.grant_trial(session, user)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"vip.grant_trial.fail_password user_id={user.user_id} err={e!r}"
            )
        await session.refresh(user)
        return user

    assert last_err is not None
    raise RuntimeError(
        f"failed to allocate unique invite_code after {INVITE_CODE_RETRY} retries"
    ) from last_err


async def register_with_password(
    session: AsyncSession,
    *,
    phone: str | None = None,
    email: str | None = None,
    password: str,
    invite_code: str | None = None,
    settings: Settings | None = None,
) -> tuple[User, IssuedTokens]:
    """密码注册 — phone OR email 二选一(或都填), 走 bcrypt hash 落库.

    流程:
    1. normalize phone / email (raise 格式错)
    2. 查 phone / email 是否已存在 (raise *AlreadyExistsError 让 user 去登录)
    3. hash 密码 + 同事务建 user + invite_code + VIP trial
    4. (optional) invite_code 同事务绑邀请人 (与 POST /invite/bind 等价)
    5. 颁发 token, 返回 (user, tokens)

    Raises:
        IdentifierFormatError: phone / email 格式错
        PhoneAlreadyExistsError / EmailAlreadyExistsError: 已被注册
        PasswordTooLongError: UTF-8 > 72 字节 (schema 应该已挡)
    """
    settings = settings or get_settings()

    # 1. normalize
    if not phone and not email:
        raise IdentifierFormatError("phone 或 email 至少填一个")
    norm_phone: str | None = None
    norm_email: str | None = None
    if phone:
        try:
            norm_phone = normalize_phone(phone)
        except InvalidPhoneError as e:
            raise IdentifierFormatError(f"invalid phone: {e.reason}") from e
    if email:
        try:
            norm_email = normalize_email(email)
        except InvalidEmailError as e:
            raise IdentifierFormatError(f"invalid email: {e.reason}") from e

    # 2. 已存在检查 (减少撞 IntegrityError 的场景)
    if norm_phone:
        existing = await user_service.find_user_by_phone(session, norm_phone)
        if existing is not None:
            raise PhoneAlreadyExistsError(
                f"phone {mask_phone(norm_phone)} already registered"
            )
    if norm_email:
        existing = await user_service.find_user_by_email(session, norm_email)
        if existing is not None:
            raise EmailAlreadyExistsError(
                f"email {mask_email(norm_email)} already registered"
            )

    # 3. hash + 建 user
    try:
        pwd_hash = hash_password(password)
    except PasswordTooLongError as e:
        # schema 应该已挡, 这里是 defense-in-depth — 转 IdentifierFormatError
        raise IdentifierFormatError("password too long after UTF-8 encoding") from e

    user = await _create_user_with_password(
        session, phone=norm_phone, email=norm_email, password_hash=pwd_hash
    )

    # 4. 邀请码 (失败不阻塞注册主路径)
    if invite_code:
        try:
            await invite_service.bind_invite(
                session, current_user=user, code=invite_code
            )
        except invite_service.InviteError as e:
            logger.info(
                f"register.invite_bind.fail user_id={user.user_id} code={invite_code} err={e!r}"
            )
            # 不抛 — 注册主路径成功就好, 邀请绑定失败让用户后续在 me 页手动重试
    else:
        # bind_invite 内部会 commit; 没邀请码时这里手动 commit 让 user 落库
        await _touch_last_active(session, user.user_id)
        await session.commit()

    logger.info(
        f"auth.register.password.ok user_id={user.user_id} "
        f"phone={mask_phone(norm_phone) if norm_phone else 'none'} "
        f"email={mask_email(norm_email) if norm_email else 'none'} "
        f"invite={'bound' if invite_code else 'none'}"
    )

    tokens = _issue_token_pair(user.user_id, settings)
    return user, tokens


async def verify_password_login(
    session: AsyncSession,
    *,
    identifier: str,
    password: str,
    settings: Settings | None = None,
) -> tuple[User, IssuedTokens]:
    """密码登录 — identifier 自动判断 phone vs email (含 @ → email).

    安全设计:
    - **所有错误统一抛 InvalidCredentialsError** (不区分 user 不存在 vs 密码错 vs
      未设密码) 防 enumeration attack
    - bcrypt verify 走常量时间, ``security_password.verify_password`` 内部已保证
    - 限流由路由 ``@rate_limit`` 装饰器负责 (5次/5min 同 identifier)

    Raises:
        InvalidCredentialsError: 用户不存在 / 密码错 / 未设密码 / 用户被禁用
    """
    settings = settings or get_settings()
    identifier = identifier.strip()

    # 1. 找用户 (按 identifier 类型分流)
    user: User | None = None
    log_id = identifier
    if looks_like_email(identifier):
        try:
            email = normalize_email(identifier)
            log_id = mask_email(email)
            user = await user_service.find_user_by_email(session, email)
        except InvalidEmailError:
            user = None
    else:
        try:
            phone = normalize_phone(identifier)
            log_id = mask_phone(phone)
            user = await user_service.find_user_by_phone(session, phone)
        except InvalidPhoneError:
            user = None

    if user is None:
        logger.info(f"auth.password.fail.no_user identifier={log_id}")
        # 即使没 user 也走一次 bcrypt 防侧信道 (timing attack 防御)
        verify_password(password, "$2b$12$" + "x" * 53)
        raise InvalidCredentialsError("identifier or password invalid")

    if user.password_hash is None:
        logger.info(f"auth.password.fail.no_password user_id={user.user_id}")
        # 同样跑一次 verify 防 timing attack
        verify_password(password, "$2b$12$" + "x" * 53)
        raise InvalidCredentialsError("identifier or password invalid")

    if user.status != USER_STATUS_ACTIVE:
        logger.info(f"auth.password.fail.disabled user_id={user.user_id}")
        raise InvalidCredentialsError("identifier or password invalid")

    if not verify_password(password, user.password_hash):
        logger.info(f"auth.password.fail.bad_password user_id={user.user_id}")
        raise InvalidCredentialsError("identifier or password invalid")

    # 2. 成功 — touch last_active + 颁 token
    await _touch_last_active(session, user.user_id)
    await session.commit()

    tokens = _issue_token_pair(user.user_id, settings)
    logger.info(f"auth.password.ok user_id={user.user_id}")
    return user, tokens


async def set_user_password(
    session: AsyncSession,
    *,
    user: User,
    password: str,
    current_password: str | None = None,
) -> None:
    """老用户首次设密 / 改密.

    规则 (拍板 q4=A 强制设密码):
    - 用户没 password_hash (老 OTP / 微信新用户): current_password 应留空,
      直接 hash + 写库
    - 用户已有 password_hash (改密路径): current_password 必填且必须验证通过

    成功后 ``user.password_hash`` 在 session 里更新, 调用方负责 commit.

    Raises:
        CurrentPasswordInvalidError: 改密时旧密码错
        PasswordTooLongError: UTF-8 > 72 字节 (schema 应该已挡)
    """
    if user.password_hash is not None:
        # 改密路径 — 必须验证旧密码
        if not current_password:
            raise CurrentPasswordInvalidError("current_password required")
        if not verify_password(current_password, user.password_hash):
            logger.info(f"auth.password.set.bad_current user_id={user.user_id}")
            raise CurrentPasswordInvalidError("current_password mismatch")

    new_hash = hash_password(password)
    user.password_hash = new_hash
    await session.flush()
    logger.info(
        f"auth.password.set.ok user_id={user.user_id} "
        f"first_time={current_password is None}"
    )


__all__ = [
    "INVITE_CODE_LENGTH",
    "CurrentPasswordInvalidError",
    "EmailAlreadyExistsError",
    "IdentifierFormatError",
    "InvalidCredentialsError",
    "IssuedTokens",
    "OTPInvalidError",
    "OTPNotFoundError",
    "PasswordAuthError",
    "PasswordNotSetError",
    "PhoneAlreadyExistsError",
    "RefreshTokenError",
    "RefreshTokenExpired",
    "RefreshTokenInvalid",
    "RefreshTokenRevoked",
    "RefreshUserUnavailable",
    "find_or_create_user_by_phone",
    "find_or_create_user_by_wechat",
    "refresh_tokens",
    "register_with_password",
    "revoke_access_token",
    "revoke_refresh_token",
    "set_user_password",
    "verify_password_login",
    "verify_phone_login",
    "verify_wechat_mp_login",
]
