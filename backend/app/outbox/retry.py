"""Bounded exponential backoff with deterministic jitter for outbox retries (S6).

The jitter is seeded from the job id and attempt so a given (job, attempt) always yields
the same delay — retries stay reproducible in tests and evaluation while still spreading
load across a fleet.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta


def compute_backoff_seconds(
    *,
    attempt: int,
    base_seconds: float,
    max_seconds: float,
    jitter_ratio: float,
    job_id: uuid.UUID,
) -> float:
    """Exponential backoff ``base * 2**(attempt-1)``, capped, with bounded jitter."""
    exponent = max(0, attempt - 1)
    raw = base_seconds * (2.0**exponent)
    capped = min(raw, max_seconds)
    if jitter_ratio <= 0:
        return capped
    # Deterministic fraction in [0, 1) from the job id and attempt.
    digest = hashlib.sha256(f"{job_id}:{attempt}".encode()).digest()
    fraction = int.from_bytes(digest[:8], "big") / float(1 << 64)
    # Symmetric jitter in [-jitter_ratio, +jitter_ratio] of the capped delay.
    jitter = capped * jitter_ratio * (2.0 * fraction - 1.0)
    return max(0.0, capped + jitter)


def next_attempt_at(
    *,
    now: datetime,
    attempt: int,
    base_seconds: float,
    max_seconds: float,
    jitter_ratio: float,
    job_id: uuid.UUID,
) -> datetime:
    delay = compute_backoff_seconds(
        attempt=attempt,
        base_seconds=base_seconds,
        max_seconds=max_seconds,
        jitter_ratio=jitter_ratio,
        job_id=job_id,
    )
    return now + timedelta(seconds=delay)
