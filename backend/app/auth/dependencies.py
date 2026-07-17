"""FastAPI auth dependencies: resolve the actor and enforce permissions."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.enums import Permission
from app.auth.jwt import TokenError
from app.auth.models import AuthenticatedUser
from app.auth.service import AuthService
from app.core.config import Settings, get_settings
from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return token.strip()


async def get_current_user(
    request: Request, session: SessionDep, settings: SettingsDep
) -> AuthenticatedUser:
    token = _bearer_token(request)
    try:
        return await AuthService(session, settings).resolve_user(token)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def require_permission(
    permission: Permission,
) -> Callable[[AuthenticatedUser], Coroutine[Any, Any, AuthenticatedUser]]:
    """Dependency factory enforcing a permission on the authenticated actor."""

    async def _dependency(user: CurrentUser) -> AuthenticatedUser:
        if not user.has(permission):
            raise HTTPException(
                status_code=403, detail=f"missing permission: {permission.value}"
            )
        return user

    return _dependency
