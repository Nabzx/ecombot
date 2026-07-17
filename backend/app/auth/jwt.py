"""JWT creation and validation (HS256).

Tokens carry user id, role, token type, issued/expiry times and a unique token id
(``jti``). The signing secret is read from settings and never logged. Invalid signature,
wrong token type, or expiry all raise :class:`TokenError`, which the API maps to 401.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.auth.enums import TokenType
from app.auth.models import TokenPayload
from app.core.config import Settings
from app.models.enums import UserRole


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired or has the wrong type."""


def _now() -> datetime:
    return datetime.now(UTC)


def create_token(
    *,
    settings: Settings,
    user_id: uuid.UUID,
    role: UserRole,
    token_type: TokenType,
    now: datetime | None = None,
) -> tuple[str, int]:
    """Return a signed token and its lifetime in seconds."""
    issued = now or _now()
    minutes = (
        settings.access_token_expire_minutes
        if token_type == TokenType.ACCESS
        else settings.refresh_token_expire_minutes
    )
    expires = issued + timedelta(minutes=minutes)
    claims = {
        "sub": str(user_id),
        "role": role.value,
        "token_type": token_type.value,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, minutes * 60


def decode_token(
    token: str, *, settings: Settings, expected_type: TokenType | None = None
) -> TokenPayload:
    """Decode and validate a token, or raise TokenError."""
    try:
        raw = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid token") from exc
    try:
        payload = TokenPayload.model_validate(raw)
    except ValueError as exc:
        raise TokenError("invalid token claims") from exc
    if expected_type is not None and payload.token_type != expected_type:
        raise TokenError(f"expected a {expected_type.value} token")
    return payload
