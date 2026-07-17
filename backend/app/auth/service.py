"""Authentication service: verify credentials and resolve the authenticated actor."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.enums import TokenType
from app.auth.jwt import TokenError, create_token, decode_token
from app.auth.models import AuthenticatedUser, TokenPair
from app.auth.passwords import verify_password
from app.core.config import Settings
from app.repositories.user import UserRepository


class AuthenticationError(Exception):
    """Raised when credentials are invalid or the user cannot authenticate."""


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def authenticate(self, email: str, password: str) -> TokenPair:
        """Verify credentials and issue an access + refresh token pair."""
        user = await UserRepository(self._session).get_by_email(email.strip().lower())
        # Verify a hash even on unknown users to avoid a trivial timing oracle.
        stored = user.hashed_password if user else "$2b$04$" + "x" * 53
        password_ok = verify_password(password, stored)
        if user is None or not password_ok:
            raise AuthenticationError("invalid email or password")
        if not user.is_active:
            raise AuthenticationError("user is inactive")
        access, expires_in = create_token(
            settings=self._settings,
            user_id=user.id,
            role=user.role,
            token_type=TokenType.ACCESS,
        )
        refresh, _ = create_token(
            settings=self._settings,
            user_id=user.id,
            role=user.role,
            token_type=TokenType.REFRESH,
        )
        return TokenPair(
            access_token=access, refresh_token=refresh, expires_in=expires_in
        )

    async def refresh(self, refresh_token: str) -> TokenPair:
        payload = decode_token(
            refresh_token, settings=self._settings, expected_type=TokenType.REFRESH
        )
        user = await UserRepository(self._session).get(uuid.UUID(payload.sub))
        if user is None or not user.is_active:
            raise AuthenticationError("user is inactive or unknown")
        access, expires_in = create_token(
            settings=self._settings,
            user_id=user.id,
            role=user.role,
            token_type=TokenType.ACCESS,
        )
        return TokenPair(access_token=access, expires_in=expires_in)

    async def resolve_user(self, access_token: str) -> AuthenticatedUser:
        """Resolve the authenticated actor from an access token (raises on problems)."""
        payload = decode_token(
            access_token, settings=self._settings, expected_type=TokenType.ACCESS
        )
        user = await UserRepository(self._session).get(uuid.UUID(payload.sub))
        if user is None:
            raise TokenError("token subject not found")
        if not user.is_active:
            raise TokenError("user is inactive")
        # The role in the token must still match the stored role (no stale privilege).
        if user.role != payload.role:
            raise TokenError("token role does not match user")
        return AuthenticatedUser.build(
            user_id=user.id,
            role=user.role,
            email=user.email,
            is_active=user.is_active,
        )
