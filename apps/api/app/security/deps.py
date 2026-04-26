"""FastAPI 鉴权依赖.

提供两个依赖:
- ``get_current_user``    强校验, token 缺失/无效/过期/用户被禁用 都直接 401
- ``get_optional_user``   允许匿名访问, 解析失败返回 ``None``, 用于 IPO 列表等
                          公开页 + 登录后展示个性化 (自选/已读) 的混合接口

设计要点:
1. **不用 ``HTTPBearer(auto_error=False)``**:
   FastAPI 自带的 HTTPBearer 在 ``auto_error=False`` 时, 把"无 Authorization header"
   与"scheme 不是 Bearer (如 Basic)"折叠成同一个 ``None``, 丢失 401 reason 信号。
   我们手动解析 ``Authorization`` header, 保留细粒度错误码。
2. 严格区分 ``token_expired`` / ``token_invalid`` / ``token_missing`` /
   ``token_scheme_invalid`` / ``user_not_found`` / ``user_disabled`` 六种 401 reason;
   前端可据此做不同 UX (过期 silent refresh, 无效跳登录, scheme 错就提示开发自查).
3. 用 ``decode_token(..., expected_type=ACCESS_TOKEN_TYPE)`` 强制 typ=access,
   refresh token 即使被复制到 Authorization header 也不被接受。
4. 解出 ``sub`` 后回查 DB 一次, 校验 ``status==1`` 且未软删; 这一步是 RBAC 的最低保障,
   即使后续加 scope 也不应跳过 (token 内可缓存的属性都不可信)。
5. ``get_current_user`` 返回 ORM ``User`` 对象, 业务层可直接 ``user.user_id`` /
   ``user.region`` 等; 上游路由要序列化时用 ``UserPublic.model_validate(user)``。

后续:
- BE-004 加 refresh / logout 时, 在本模块加 ``get_token_payload`` (不查 DB,
  只解 token + jti), 给 logout / blacklist 用。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import User
from app.security.blacklist import is_jti_blacklisted
from app.security.jwt import (
    ACCESS_TOKEN_TYPE,
    AccessTokenPayload,
    InvalidTokenError,
    TokenExpiredError,
    decode_token,
)
from app.services import user_service

USER_STATUS_ACTIVE = 1


@dataclass(frozen=True, slots=True)
class _ParsedAuth:
    """``Authorization`` header 解析结果. 用 enum-like state 替代多 None 字段。"""

    state: str  # "missing" | "scheme_invalid" | "ok"
    token: str = ""


def _unauthorized(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": message},
        headers={"WWW-Authenticate": 'Bearer realm="xgzh"'},
    )


def _parse_authorization(header_value: str | None) -> _ParsedAuth:
    """解析 ``Authorization: Bearer xxx``. 不抛异常, 把状态用 dataclass 返回."""
    if header_value is None or not header_value.strip():
        return _ParsedAuth(state="missing")
    parts = header_value.strip().split(None, 1)  # 拆出 scheme + rest, 容忍多空格
    if len(parts) != 2:
        return _ParsedAuth(state="scheme_invalid")
    scheme, rest = parts
    if scheme.lower() != "bearer":
        return _ParsedAuth(state="scheme_invalid")
    token = rest.strip()
    if not token:
        return _ParsedAuth(state="scheme_invalid")
    return _ParsedAuth(state="ok", token=token)


async def _resolve_user_from_token(token: str, session: AsyncSession) -> User:
    """解 access token + 查黑名单 + 查 DB. 失败抛 ``HTTPException(401)``。"""
    try:
        payload = decode_token(token, expected_type=ACCESS_TOKEN_TYPE)
    except TokenExpiredError as e:
        raise _unauthorized("token_expired", "access token 已过期, 请用 refresh 续签") from e
    except InvalidTokenError as e:
        raise _unauthorized("token_invalid", str(e)) from e

    assert isinstance(payload, AccessTokenPayload)

    # logout 后即时失效: jti 黑名单命中直接 401, 不再查 DB (省一次 SQL)
    if await is_jti_blacklisted(payload.jti):
        logger.info(f"auth.deps.token_revoked jti={payload.jti} user_id={payload.user_id}")
        raise _unauthorized("token_revoked", "token 已注销, 请重新登录")

    user = await user_service.find_user_by_id(session, payload.user_id)
    if user is None:
        logger.info(f"auth.deps.user_not_found user_id={payload.user_id}")
        raise _unauthorized("user_not_found", "用户不存在或已注销")
    if user.status != USER_STATUS_ACTIVE:
        logger.warning(f"auth.deps.user_disabled user_id={user.user_id} status={user.status}")
        raise _unauthorized("user_disabled", "账户已被禁用")

    return user


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    """强校验依赖. 任何 401 reason 都直接 raise."""
    parsed = _parse_authorization(request.headers.get("authorization"))
    if parsed.state == "missing":
        raise _unauthorized("token_missing", "缺少 Authorization header")
    if parsed.state == "scheme_invalid":
        raise _unauthorized(
            "token_scheme_invalid",
            "Authorization scheme 必须为 Bearer",
        )
    return await _resolve_user_from_token(parsed.token, session)


async def get_optional_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User | None:
    """匿名友好依赖. 没 token 直接 ``None``; 有 token 但解析失败也 ``None`` (打 debug 日志)。

    适用接口: IPO 列表 (匿名可看, 登录后高亮自选) / Agent 试用 (匿名 1 次/天)。
    """
    parsed = _parse_authorization(request.headers.get("authorization"))
    if parsed.state != "ok":
        return None
    try:
        return await _resolve_user_from_token(parsed.token, session)
    except HTTPException as e:
        logger.debug(
            f"auth.deps.optional.fail path={request.url.path} reason={e.detail}"
        )
        return None


__all__ = [
    "USER_STATUS_ACTIVE",
    "get_current_user",
    "get_optional_user",
]
