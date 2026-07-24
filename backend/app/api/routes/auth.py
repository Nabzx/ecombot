"""Authentication endpoints: login, me, refresh."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.auth.models import TokenPair
from app.auth.service import AuthenticationError, AuthService
from app.core.config import Settings, get_settings
from app.db.session import get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class RefreshBody(BaseModel):
    refresh_token: str = Field(min_length=1)


class MeResponse(BaseModel):
    user_id: str
    email: str
    role: str
    permissions: list[str]


@router.post("/login")
async def login(
    body: LoginBody, session: SessionDep, settings: SettingsDep
) -> TokenPair:
    from datetime import UTC, datetime

    from app.audit.enums import AuditEventType
    from app.audit.service import AuditService

    now = datetime.now(UTC)
    try:
        pair = await AuthService(session, settings).authenticate(
            body.email, body.password
        )
    except AuthenticationError as exc:
        # A failed login is a security-relevant event; record it (no PII, no password).
        await AuditService(session).record(
            AuditEventType.AUTH_LOGIN_FAILED,
            occurred_at=now,
            subject_type="auth",
            actor_role="anonymous",
            summary="login failed",
        )
        await session.commit()
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    await AuditService(session).record(
        AuditEventType.AUTH_LOGIN_SUCCEEDED,
        occurred_at=now,
        subject_type="auth",
        actor_role="user",
        summary="login succeeded",
    )
    await session.commit()
    return pair


@router.post("/refresh")
async def refresh(
    body: RefreshBody, session: SessionDep, settings: SettingsDep
) -> TokenPair:
    try:
        return await AuthService(session, settings).refresh(body.refresh_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:  # invalid/expired refresh token
        raise HTTPException(status_code=401, detail="invalid refresh token") from exc


@router.get("/me")
async def me(user: CurrentUser) -> MeResponse:
    return MeResponse(
        user_id=str(user.user_id),
        email=user.email,
        role=user.role.value,
        permissions=sorted(p.value for p in user.permissions),
    )
