"""Customer schemas.

The summary masks PII (used in lists/queues); the detail exposes full contact data
for agents who legitimately need it to resolve identity within a case.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.models.enums import CustomerTier
from app.schemas.common import ORMModel


class CustomerSummary(ORMModel):
    id: uuid.UUID
    external_reference: str
    full_name: str
    masked_email: str
    masked_phone: str
    tier: CustomerTier


class CustomerDetail(ORMModel):
    id: uuid.UUID
    external_reference: str
    first_name: str
    last_name: str
    full_name: str
    email: str
    phone: str
    tier: CustomerTier
    created_at: datetime
    updated_at: datetime
