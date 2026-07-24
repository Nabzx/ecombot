"""Canonical hashing and chain verification for the audit log (S7).

``entry_hash = sha256(canonical(entry_without_hashes) + previous_hash)``. The genesis
``previous_hash`` is a fixed constant. Because each entry commits to its predecessor's
hash, any inserted, deleted or altered row breaks the chain from that point on.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS_HASH = "0" * 64

# Fields (in order) that are covered by the entry hash. Volatile/derived columns
# (id, entry_hash itself) are excluded.
_HASHED_FIELDS = (
    "sequence",
    "event_type",
    "actor_user_id",
    "actor_role",
    "subject_type",
    "subject_id",
    "correlation_id",
    "summary",
    "metadata_json",
    "previous_hash",
    "occurred_at",
)


def canonical_payload(fields: dict[str, Any]) -> str:
    ordered = {key: fields.get(key) for key in _HASHED_FIELDS}
    return json.dumps(ordered, sort_keys=True, ensure_ascii=True, default=str)


def compute_entry_hash(fields: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(fields).encode("utf-8")).hexdigest()
