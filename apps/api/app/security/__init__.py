"""鉴权 / token / 加密 工具。"""

from app.security.blacklist import blacklist_jti, is_jti_blacklisted
from app.security.deps import get_current_user, get_optional_user
from app.security.jwt import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    AccessTokenPayload,
    InvalidTokenError,
    RefreshTokenPayload,
    TokenExpiredError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)

__all__ = [
    "ACCESS_TOKEN_TYPE",
    "AccessTokenPayload",
    "InvalidTokenError",
    "REFRESH_TOKEN_TYPE",
    "RefreshTokenPayload",
    "TokenExpiredError",
    "TokenType",
    "blacklist_jti",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_current_user",
    "get_optional_user",
    "is_jti_blacklisted",
]
