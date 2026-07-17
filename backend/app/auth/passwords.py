"""Password verification for authentication (bcrypt).

Hashes are only ever verified here; plaintext passwords and hashes never appear in
responses or logs.
"""

from __future__ import annotations

import bcrypt


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash (never raises)."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):  # pragma: no cover - malformed stored hash
        return False
