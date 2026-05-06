"""鉴权路由 (spec/03 §6).

Sprint 1:
- BE-001: ``POST /auth/otp/send``     发送 OTP
- BE-002: ``POST /auth/login/phone``  OTP 校验 + 注册/登录 + JWT
- BE-004: ``POST /auth/refresh``       Refresh token rotation
- BE-004: ``POST /auth/logout``        拉黑 access (+ 可选 refresh)
- BE-005: ``POST /auth/login/wechat-mp`` 小程序 code → openid/unionid → 注册/登录
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.sms import SMSDeliveryError
from app.adapters.wechat import (
    WechatAPIError,
    WechatAuthError,
    get_wechat_mp_client,
)
from app.cache import rate_limit
from app.core.config import get_settings
from app.db import get_session
from app.db.models import User
from app.schemas.auth import (
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    OTPSendRequest,
    OTPSendResponse,
    PasswordLoginRequest,
    PasswordRegisterRequest,
    PhoneLoginRequest,
    RefreshRequest,
    TokenPair,
    UserPublic,
    WechatMpLoginRequest,
)
from app.security import (
    ACCESS_TOKEN_TYPE,
    AccessTokenPayload,
    InvalidTokenError,
    TokenExpiredError,
    decode_token,
    get_current_user,
)
from app.services import auth_service, otp_service
from app.services.auth_service import (
    EmailAlreadyExistsError,
    IdentifierFormatError,
    InvalidCredentialsError,
    OTPInvalidError,
    OTPNotFoundError,
    PhoneAlreadyExistsError,
    RefreshTokenExpired,
    RefreshTokenInvalid,
    RefreshTokenRevoked,
    RefreshUserUnavailable,
)
from app.utils.phone import InvalidPhoneError, mask_phone, normalize_phone

router = APIRouter(prefix="/auth", tags=["auth"])


def _phone_rate_limit_key(req: OTPSendRequest, **_: object) -> str:
    """用 已归一化 的 phone 作为限流 key, 避免 ``13800138000`` 与 ``+8613800138000`` 绕过.

    ``**_`` 兜底: FastAPI 会把所有依赖也以 kwarg 形式塞进 wrapper, 无视即可。
    """
    try:
        return f"phone:{normalize_phone(req.phone)}"
    except InvalidPhoneError:
        return f"phone:invalid:{req.phone[:32]}"


@router.post(
    "/otp/send",
    response_model=OTPSendResponse,
    status_code=status.HTTP_200_OK,
    summary="发送手机 OTP",
    responses={
        400: {"description": "手机号格式不合法 / 不在支持区域"},
        429: {"description": "同手机号 60 秒限流"},
        502: {"description": "SMS 通道失败"},
    },
)
@rate_limit(
    times=1,
    per_seconds=60,
    namespace="otp_send",
    key_func=_phone_rate_limit_key,
)
async def send_otp(req: OTPSendRequest) -> OTPSendResponse:
    settings = get_settings()
    try:
        phone = normalize_phone(req.phone)
    except InvalidPhoneError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_phone", "message": e.reason},
        ) from e

    try:
        result = await otp_service.send_otp(phone, ttl_seconds=settings.otp_ttl_seconds)
    except SMSDeliveryError as e:
        logger.warning(f"otp.send.fail phone={mask_phone(phone)} provider={e.provider} {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "sms_delivery_failed", "message": str(e)},
        ) from e

    return OTPSendResponse(
        sent=True,
        expires_in=settings.otp_ttl_seconds,
        request_id=result.request_id,
        masked_phone=mask_phone(phone),
    )


def _phone_verify_rate_limit_key(req: PhoneLoginRequest, **_: object) -> str:
    """同手机号 5 次/5min verify 限流 (防暴力试码). 与 send 限流分桶 (namespace 不同)."""
    try:
        return f"phone:{normalize_phone(req.phone)}"
    except InvalidPhoneError:
        return f"phone:invalid:{req.phone[:32]}"


@router.post(
    "/login/phone",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="OTP 校验 + 注册/登录 + 颁发 JWT",
    responses={
        400: {"description": "手机号格式不合法"},
        401: {"description": "OTP 错误 / 已过期 / 已被消费"},
        429: {"description": "5 分钟内同手机号验证次数过多"},
    },
)
@rate_limit(
    times=5,
    per_seconds=300,
    namespace="otp_verify",
    key_func=_phone_verify_rate_limit_key,
)
async def login_phone(
    req: PhoneLoginRequest,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    try:
        phone = normalize_phone(req.phone)
    except InvalidPhoneError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_phone", "message": e.reason},
        ) from e

    try:
        user, is_new, tokens = await auth_service.verify_phone_login(
            session, phone=phone, code=req.code
        )
    except OTPNotFoundError as e:
        logger.info(f"auth.login.fail.no_otp phone={mask_phone(phone)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "otp_expired",
                "message": "OTP 不存在或已过期, 请重新获取",
            },
        ) from e
    except OTPInvalidError as e:
        logger.info(f"auth.login.fail.bad_otp phone={mask_phone(phone)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "otp_invalid", "message": "验证码错误"},
        ) from e

    return LoginResponse(
        user=UserPublic.model_validate(user),
        tokens=TokenPair(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=tokens.access_expires_in,
            refresh_expires_in=tokens.refresh_expires_in,
        ),
        is_new_user=is_new,
    )


# ----------------------------- BE-004 -----------------------------


def _refresh_rate_limit_key(req: RefreshRequest, **_: object) -> str:
    """同 refresh_token 5次/分钟 防重放刷, 直接拿前 32 字符 hash 当 key 即可."""
    return f"token:{req.refresh_token[:32]}"


@router.post(
    "/refresh",
    response_model=TokenPair,
    status_code=status.HTTP_200_OK,
    summary="Refresh token rotation: 旧 refresh 拉黑, 颁发新 access+refresh",
    responses={
        401: {"description": "refresh 无效 / 过期 / 被拉黑 / 用户不可用"},
        429: {"description": "同一 refresh_token 1 分钟内刷新次数过多"},
    },
)
@rate_limit(
    times=5,
    per_seconds=60,
    namespace="token_refresh",
    key_func=_refresh_rate_limit_key,
)
async def refresh_token(
    req: RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenPair:
    try:
        _, tokens = await auth_service.refresh_tokens(
            session, refresh_token=req.refresh_token
        )
    except RefreshTokenExpired as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "token_expired", "message": "refresh token 已过期, 请重新登录"},
        ) from e
    except RefreshTokenRevoked as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "token_revoked", "message": "refresh token 已注销, 请重新登录"},
        ) from e
    except RefreshUserUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "user_unavailable", "message": "用户不存在或已被禁用"},
        ) from e
    except RefreshTokenInvalid as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "token_invalid", "message": str(e)},
        ) from e

    return TokenPair(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.access_expires_in,
        refresh_expires_in=tokens.refresh_expires_in,
    )


def _extract_bearer_token(request: Request) -> str | None:
    """从 ``Authorization: Bearer xxx`` 抓 token, 容错; 不抛异常.

    路由的 ``Depends(get_current_user)`` 已经验证过 header 合法, 所以这里能拿到就一定有值,
    但单独保留这个函数, 便于将来加 ``logout`` 的"匿名也能调用"分支 (清自己的 cookie)。
    """
    raw = request.headers.get("authorization")
    if not raw:
        return None
    parts = raw.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


@router.post(
    "/logout",
    response_model=LogoutResponse,
    status_code=status.HTTP_200_OK,
    summary="登出: 拉黑当前 access (+ 可选 refresh)",
    responses={
        401: {"description": "未登录 / access 已失效"},
    },
)
async def logout(
    request: Request,
    body: LogoutRequest | None = None,
    current_user: User = Depends(get_current_user),
) -> LogoutResponse:
    body = body or LogoutRequest()
    revoked_access = False
    revoked_refresh = False

    raw_token = _extract_bearer_token(request)
    if raw_token:
        try:
            access_payload = decode_token(raw_token, expected_type=ACCESS_TOKEN_TYPE)
        except (InvalidTokenError, TokenExpiredError):
            access_payload = None  # 不该发生 (get_current_user 已校验), 但兜底
        else:
            assert isinstance(access_payload, AccessTokenPayload)
            revoked_access = await auth_service.revoke_access_token(
                access_payload, reason="logout"
            )

    if body.refresh_token:
        revoked_refresh = await auth_service.revoke_refresh_token(
            body.refresh_token,
            expected_user_id=current_user.user_id,
            reason="logout",
        )

    logger.info(
        f"auth.logout user_id={current_user.user_id} "
        f"revoked_access={revoked_access} revoked_refresh={revoked_refresh}"
    )
    return LogoutResponse(
        logged_out=True,
        revoked_access=revoked_access,
        revoked_refresh=revoked_refresh,
    )


# ----------------------------- BE-005 -----------------------------


def _wechat_code_rate_limit_key(req: WechatMpLoginRequest, **_: object) -> str:
    """同一 code 1 分钟 5 次防暴力 (理论上 wx.login 的 code 只能用一次, 这是多重保险)."""
    return f"code:{req.code[:32]}"


@router.post(
    "/login/wechat-mp",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="微信小程序登录: code → openid/unionid → 注册/登录 → JWT",
    responses={
        401: {"description": "code 无效 / 已被使用 / 已过期"},
        429: {"description": "同一 code 1 分钟内尝试次数过多"},
        502: {"description": "微信侧故障 / 我方 AppSecret 配置错"},
        503: {"description": "服务未启用 (WECHAT_MP_APP_ID/SECRET 未配置)"},
    },
)
@rate_limit(
    times=5,
    per_seconds=60,
    namespace="wechat_mp_login",
    key_func=_wechat_code_rate_limit_key,
)
async def login_wechat_mp(
    req: WechatMpLoginRequest,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    settings = get_settings()
    if not settings.wechat_mp_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "wechat_mp_not_configured",
                "message": "微信小程序登录暂未启用, 请使用手机号登录",
            },
        )

    client = get_wechat_mp_client(settings)
    try:
        result = await client.code2session(req.code)
    except WechatAuthError as e:
        logger.info(f"auth.wechat.code_invalid errcode={e.errcode}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "wechat_code_invalid",
                "message": "微信 code 无效或已过期, 请重新获取",
                "errcode": e.errcode,
            },
        ) from e
    except WechatAPIError as e:
        logger.warning(f"auth.wechat.api_error errcode={e.errcode} {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "wechat_upstream_error",
                "message": "微信服务暂时不可用, 请稍后重试",
                "errcode": e.errcode,
            },
        ) from e

    try:
        user, is_new, tokens = await auth_service.verify_wechat_mp_login(
            session, openid=result.openid, unionid=result.unionid
        )
    except RefreshUserUnavailable as e:
        # 老用户被禁用 / 封号; 不能借微信登录绕过
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "user_disabled", "message": "账户已被禁用"},
        ) from e

    return LoginResponse(
        user=UserPublic.model_validate(user),
        tokens=TokenPair(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=tokens.access_expires_in,
            refresh_expires_in=tokens.refresh_expires_in,
        ),
        is_new_user=is_new,
    )


# ----------------------------- BUG-S9-001 密码注册 / 登录 -----------------------------


def _password_login_rate_limit_key(req: PasswordLoginRequest, **_: object) -> str:
    """同 identifier 5次/5min 防暴力试密码. 用 strip + lower 做粗 normalize,
    精确归一在 service 层做 (这里是限流 bucket key, 不需精确)."""
    return f"identifier:{req.identifier.strip().lower()[:64]}"


def _password_register_rate_limit_key(
    req: PasswordRegisterRequest, **_: object
) -> str:
    """注册场景的限流: 同 identifier 5次/小时, 防恶意刷注册."""
    ident = req.phone or req.email or ""
    return f"register:{ident.strip().lower()[:64]}"


@router.post(
    "/register/password",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="密码注册 (phone OR email + password)",
    responses={
        400: {"description": "格式错 (phone / email / password 校验失败)"},
        409: {"description": "phone / email 已存在"},
        429: {"description": "同 identifier 1 小时内注册尝试过多"},
    },
)
@rate_limit(
    times=5,
    per_seconds=3600,
    namespace="password_register",
    key_func=_password_register_rate_limit_key,
)
async def register_with_password(
    req: PasswordRegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    """密码注册 — phone 或 email 二选一(都填也允许), 走 bcrypt hash 落库.

    成功直接返回 LoginResponse + token, 用户立刻进入登录态. invite_code 可选,
    成功绑定后给 referrer +1 invitee count (可能触发邀请奖励).
    """
    try:
        user, tokens = await auth_service.register_with_password(
            session,
            phone=req.phone,
            email=req.email,
            password=req.password,
            invite_code=req.invite_code,
        )
    except IdentifierFormatError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "identifier_format_invalid", "message": str(e)},
        ) from e
    except PhoneAlreadyExistsError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "phone_already_exists",
                "message": "该手机号已注册, 请直接登录",
            },
        ) from e
    except EmailAlreadyExistsError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "email_already_exists",
                "message": "该邮箱已注册, 请直接登录",
            },
        ) from e

    return LoginResponse(
        user=UserPublic.model_validate(user),
        tokens=TokenPair(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=tokens.access_expires_in,
            refresh_expires_in=tokens.refresh_expires_in,
        ),
        is_new_user=True,
    )


@router.post(
    "/login/password",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="密码登录 (phone 或 email + password 自动识别)",
    responses={
        401: {"description": "凭据无效 (统一返 invalid_credentials 防 enumeration)"},
        429: {"description": "同 identifier 5 分钟内尝试过多"},
    },
)
@rate_limit(
    times=5,
    per_seconds=300,
    namespace="password_login",
    key_func=_password_login_rate_limit_key,
)
async def login_with_password(
    req: PasswordLoginRequest,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    """密码登录. identifier 自动判断 (含 @ → email; 否则 phone).

    所有错误统一返 401 ``invalid_credentials`` 防 enumeration attack;
    bcrypt verify 走常量时间 + 即使 user 不存在也跑一次 dummy verify 防侧信道.
    """
    try:
        user, tokens = await auth_service.verify_password_login(
            session, identifier=req.identifier, password=req.password
        )
    except InvalidCredentialsError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "invalid_credentials",
                "message": "账号或密码错误",
            },
        ) from e

    return LoginResponse(
        user=UserPublic.model_validate(user),
        tokens=TokenPair(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=tokens.access_expires_in,
            refresh_expires_in=tokens.refresh_expires_in,
        ),
        is_new_user=False,
    )
