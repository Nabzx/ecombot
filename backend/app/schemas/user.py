"""User schemas. The password hash is never exposed."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.models.enums import UserRole
from app.schemas.common import ORMModel


class UserSummary(ORMModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
