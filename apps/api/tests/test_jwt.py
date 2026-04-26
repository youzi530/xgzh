"""``app.security.jwt`` 纯单元测试.

不依赖 DB / Redis / FastAPI; 跑在所有环境里 (含 CI 不开 DB 的快速通道)。
"""

from __future__ import annotations

import time
import uuid

import jwt as pyjwt
import pytest

from app.core.config import Settings
from app.security import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    AccessTokenPayload,
    InvalidTokenError,
    RefreshTokenPayload,
    TokenExpiredError,
    create_access_token,
    create_refresh_token,
    decode_token,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        jwt_secret="test-secret-32bytes-aaaaaaaaaaaaaaaaaaa",
        jwt_algorithm="HS256",
        jwt_issuer="xgzh-api",
        jwt_audience="xgzh-mp",
        jwt_access_ttl_seconds=1800,
        jwt_refresh_ttl_seconds=2592000,
    )


def test_create_access_token_decodes(settings: Settings) -> None:
    uid = uuid.uuid4()
    token, payload = create_access_token(uid, settings)
    assert isinstance(payload, AccessTokenPayload)
    assert payload.user_id == uid
    assert payload.expires_at - payload.issued_at == 1800

    decoded = decode_token(token, expected_type=ACCESS_TOKEN_TYPE, settings=settings)
    assert isinstance(decoded, AccessTokenPayload)
    assert decoded.user_id == uid
    assert decoded.jti == payload.jti


def test_create_refresh_token_decodes(settings: Settings) -> None:
    uid = uuid.uuid4()
    token, payload = create_refresh_token(uid, settings)
    assert isinstance(payload, RefreshTokenPayload)
    assert payload.expires_at - payload.issued_at == 30 * 24 * 3600

    decoded = decode_token(token, expected_type=REFRESH_TOKEN_TYPE, settings=settings)
    assert isinstance(decoded, RefreshTokenPayload)
    assert decoded.user_id == uid


def test_typ_mismatch_raises(settings: Settings) -> None:
    """access typ 解 refresh, 反之亦然, 必须 InvalidTokenError."""
    uid = uuid.uuid4()
    a, _ = create_access_token(uid, settings)
    r, _ = create_refresh_token(uid, settings)

    with pytest.raises(InvalidTokenError):
        decode_token(a, expected_type=REFRESH_TOKEN_TYPE, settings=settings)
    with pytest.raises(InvalidTokenError):
        decode_token(r, expected_type=ACCESS_TOKEN_TYPE, settings=settings)


def test_tampered_token_signature_fails(settings: Settings) -> None:
    uid = uuid.uuid4()
    token, _ = create_access_token(uid, settings)
    bad = token[:-1] + ("0" if token[-1] != "0" else "1")
    with pytest.raises(InvalidTokenError):
        decode_token(bad, expected_type=ACCESS_TOKEN_TYPE, settings=settings)


def test_wrong_secret_fails(settings: Settings) -> None:
    uid = uuid.uuid4()
    token, _ = create_access_token(uid, settings)
    other = Settings(
        jwt_secret="DIFFERENT-32bytes-aaaaaaaaaaaaaaaaaaaaa",
        jwt_algorithm="HS256",
        jwt_issuer=settings.jwt_issuer,
        jwt_audience=settings.jwt_audience,
    )
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type=ACCESS_TOKEN_TYPE, settings=other)


def test_wrong_audience_fails(settings: Settings) -> None:
    uid = uuid.uuid4()
    token, _ = create_access_token(uid, settings)
    other = Settings(
        jwt_secret=settings.jwt_secret,
        jwt_algorithm="HS256",
        jwt_issuer=settings.jwt_issuer,
        jwt_audience="someone-else",
    )
    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type=ACCESS_TOKEN_TYPE, settings=other)


def test_expired_token_raises_token_expired_error(settings: Settings) -> None:
    s = Settings(
        jwt_secret=settings.jwt_secret,
        jwt_algorithm="HS256",
        jwt_issuer=settings.jwt_issuer,
        jwt_audience=settings.jwt_audience,
        jwt_access_ttl_seconds=-10,
    )
    uid = uuid.uuid4()
    token, _ = create_access_token(uid, s)
    with pytest.raises(TokenExpiredError):
        decode_token(token, expected_type=ACCESS_TOKEN_TYPE, settings=s)


def test_missing_required_claim_fails(settings: Settings) -> None:
    """缺 jti / typ / sub / aud / iss / iat / exp 任一都失败."""
    now = int(time.time())
    raw = pyjwt.encode(
        {
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "sub": str(uuid.uuid4()),
            "iat": now,
            "exp": now + 60,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(InvalidTokenError):
        decode_token(raw, expected_type=ACCESS_TOKEN_TYPE, settings=settings)


def test_invalid_sub_uuid_fails(settings: Settings) -> None:
    now = int(time.time())
    raw = pyjwt.encode(
        {
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "sub": "not-a-uuid",
            "typ": ACCESS_TOKEN_TYPE,
            "jti": "abc",
            "iat": now,
            "exp": now + 60,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(InvalidTokenError):
        decode_token(raw, expected_type=ACCESS_TOKEN_TYPE, settings=settings)


def test_unique_jti_per_call(settings: Settings) -> None:
    uid = uuid.uuid4()
    seen = {create_access_token(uid, settings)[1].jti for _ in range(20)}
    assert len(seen) == 20
