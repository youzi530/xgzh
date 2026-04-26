"""JWT 颁发与解析 (HS256).

为什么自己包一层而不是直接 ``import jwt``?
- 强制带 ``iss`` / ``aud`` / ``typ`` / ``jti`` claim, 不留侥幸
- ``access`` / ``refresh`` 用 ``typ`` 字段隔离, 防止 refresh 当 access 用
- 把 ``ExpiredSignatureError`` / ``InvalidTokenError`` 重新包成项目自家异常,
  路由层统一捕获, 不耦合三方库类型
- ``decode_token`` 默认强制校验所有要素, 单点严格

Claim 约定:
    iss   -> settings.jwt_issuer        (固定 ``xgzh-api``)
    aud   -> settings.jwt_audience      (固定 ``xgzh-mp``)
    sub   -> str(user_id)               (UUID 字符串)
    typ   -> ``access`` | ``refresh``
    jti   -> uuid4 hex (refresh 用作黑名单 key, access 也带便于审计追踪)
    iat   -> issued at  (int epoch)
    exp   -> expires at (int epoch)

注意:
- ``access`` token 不带任何业务 scope, RBAC 在 BE-003+ 通过 ``current_user`` 解决
- ``refresh`` token 必须只走 ``POST /auth/refresh``, 上层 dep 严格按 ``typ`` 拒绝
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Final, Literal

import jwt
from loguru import logger

from app.core.config import Settings, get_settings

TokenType = Literal["access", "refresh"]

ACCESS_TOKEN_TYPE: Final[TokenType] = "access"
REFRESH_TOKEN_TYPE: Final[TokenType] = "refresh"

_DEV_SECRET_PREFIX = "dev-only-do-not-use-in-prod"
_MIN_PROD_SECRET_LENGTH = 32


class InvalidTokenError(Exception):
    """Token 签名错 / 缺 claim / typ 不匹配 / aud 不匹配 等."""


class TokenExpiredError(InvalidTokenError):
    """Token 已过期 (单独子类便于路由区分 401 reason)."""


@dataclass(frozen=True, slots=True)
class AccessTokenPayload:
    user_id: uuid.UUID
    jti: str
    issued_at: int
    expires_at: int
    typ: TokenType = ACCESS_TOKEN_TYPE


@dataclass(frozen=True, slots=True)
class RefreshTokenPayload:
    user_id: uuid.UUID
    jti: str
    issued_at: int
    expires_at: int
    typ: TokenType = REFRESH_TOKEN_TYPE


def _warn_if_dev_secret(settings: Settings) -> None:
    """非 dev 环境用占位 secret / 长度过短 时打 ERROR 但不 raise (避免线上突然 500)."""
    secret = settings.jwt_secret
    is_dev = settings.app_env.lower() in {"dev", "test", "local"}
    if secret.startswith(_DEV_SECRET_PREFIX):
        if not is_dev:
            logger.error(
                f"JWT_SECRET 仍是 dev 占位值, env={settings.app_env}; 立即设置真随机 secret"
            )
        return
    if len(secret) < _MIN_PROD_SECRET_LENGTH and not is_dev:
        logger.error(
            f"JWT_SECRET 长度 {len(secret)} < {_MIN_PROD_SECRET_LENGTH}, env={settings.app_env}; "
            "建议 openssl rand -hex 32"
        )


def _new_jti() -> str:
    return secrets.token_urlsafe(16)


def _build_payload(
    *,
    user_id: uuid.UUID,
    typ: TokenType,
    ttl_seconds: int,
    settings: Settings,
    jti: str | None = None,
) -> tuple[dict[str, Any], int, int, str]:
    now = int(time.time())
    exp = now + ttl_seconds
    jti = jti or _new_jti()
    payload: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "typ": typ,
        "jti": jti,
        "iat": now,
        "exp": exp,
    }
    return payload, now, exp, jti


def create_access_token(
    user_id: uuid.UUID,
    settings: Settings | None = None,
) -> tuple[str, AccessTokenPayload]:
    settings = settings or get_settings()
    _warn_if_dev_secret(settings)
    payload, iat, exp, jti = _build_payload(
        user_id=user_id,
        typ=ACCESS_TOKEN_TYPE,
        ttl_seconds=settings.jwt_access_ttl_seconds,
        settings=settings,
    )
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, AccessTokenPayload(user_id=user_id, jti=jti, issued_at=iat, expires_at=exp)


def create_refresh_token(
    user_id: uuid.UUID,
    settings: Settings | None = None,
) -> tuple[str, RefreshTokenPayload]:
    settings = settings or get_settings()
    _warn_if_dev_secret(settings)
    payload, iat, exp, jti = _build_payload(
        user_id=user_id,
        typ=REFRESH_TOKEN_TYPE,
        ttl_seconds=settings.jwt_refresh_ttl_seconds,
        settings=settings,
    )
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, RefreshTokenPayload(user_id=user_id, jti=jti, issued_at=iat, expires_at=exp)


def decode_token(
    token: str,
    expected_type: TokenType,
    settings: Settings | None = None,
) -> AccessTokenPayload | RefreshTokenPayload:
    """解码并强校验 ``iss`` / ``aud`` / ``typ`` / ``exp``.

    Raises:
        TokenExpiredError: 已过期
        InvalidTokenError: 签名 / claim / typ 任一不符
    """
    settings = settings or get_settings()
    try:
        decoded = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={
                "require": ["iss", "aud", "sub", "typ", "jti", "iat", "exp"],
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
            },
        )
    except jwt.ExpiredSignatureError as e:
        raise TokenExpiredError("token expired") from e
    except jwt.InvalidTokenError as e:  # 父类, 覆盖 InvalidAudience/Issuer/Signature/MissingRequired 等
        raise InvalidTokenError(str(e)) from e

    typ = decoded.get("typ")
    if typ != expected_type:
        raise InvalidTokenError(f"token typ mismatch: got {typ!r}, expected {expected_type!r}")

    sub = decoded.get("sub")
    try:
        user_id = uuid.UUID(str(sub))
    except (TypeError, ValueError) as e:
        raise InvalidTokenError(f"invalid sub (not UUID): {sub!r}") from e

    common = {
        "user_id": user_id,
        "jti": decoded["jti"],
        "issued_at": int(decoded["iat"]),
        "expires_at": int(decoded["exp"]),
    }
    if expected_type == ACCESS_TOKEN_TYPE:
        return AccessTokenPayload(**common)
    return RefreshTokenPayload(**common)
