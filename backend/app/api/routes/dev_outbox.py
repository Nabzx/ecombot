"""Environment-gated development endpoint to process one outbox job (S6).

This router is registered **only** in development/test (see ``app.main``), so it is
absent in production, returning 404. It runs one tick of the exact worker service; there
is no production path that executes an action over HTTP.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.dependencies import CurrentUser
from app.auth.enums import Permission
from app.core.config import Settings, get_settings
from app.outbox.worker import OutboxWorker
from app.rules.clock import seed_reference_clock

router = APIRouter(prefix="/api/dev", tags=["dev"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


class ProcessOneResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processed: int
    by_outcome: dict[str, int]


@router.post("/outbox/process-one")
async def process_one(user: CurrentUser, settings: SettingsDep) -> ProcessOneResult:
    if settings.environment not in ("development", "test"):
        raise HTTPException(status_code=404, detail="not found")
    if not user.has(Permission.OUTBOX_INSPECT):
        raise HTTPException(status_code=403, detail="missing permission")
    engine = create_async_engine(settings.database_url_str)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        worker = OutboxWorker(factory, settings=settings, clock=seed_reference_clock())
        processed = await worker.run_once()
        return ProcessOneResult(processed=processed, by_outcome=worker.stats.by_outcome)
    finally:
        await engine.dispose()
