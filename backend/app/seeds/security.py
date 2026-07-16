"""Development-only password hashing for seeded users.

Uses bcrypt at a low cost factor: these are synthetic accounts in a dev database, and
the low cost keeps seeding and tests fast. Real authentication (and an appropriate cost
factor) is wired up in a later stage.
"""

from __future__ import annotations

import bcrypt

# Every seeded user shares this obviously-fake development password.
DEV_PASSWORD = "agentops-dev"  # noqa: S105 - labelled dev-only seed password
_DEV_COST = 4


def hash_dev_password(password: str = DEV_PASSWORD) -> str:
    """Return a bcrypt hash suitable only for the seeded development database."""
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=_DEV_COST)
    ).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
