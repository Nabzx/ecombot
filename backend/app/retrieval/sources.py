"""Canonical policy sources and their trust classification.

Official policy bodies live in ``data/policies/*.md`` (loaded by the S1 seed). Isolated
test fixtures — hostile content and a future-dated policy — live under
``data/policies/fixtures/`` with YAML-style front matter and are clearly separated so
they can never be mistaken for official policy.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.core.paths import get_policies_dir
from app.models.enums import PolicySourceType, PolicyStatus
from app.seeds.policies import CONFLICT_FIXTURE_TOPIC

# Topics whose seeded versions are not ordinary official policy.
_SOURCE_TYPE_BY_TOPIC: dict[str, PolicySourceType] = {
    CONFLICT_FIXTURE_TOPIC: PolicySourceType.test_conflict,
}


def source_type_for_topic(topic: str) -> PolicySourceType:
    return _SOURCE_TYPE_BY_TOPIC.get(topic, PolicySourceType.official_policy)


@dataclass(frozen=True, slots=True)
class PolicySource:
    """A canonical policy version to (re)index."""

    topic: str
    title: str
    description: str
    version: int
    status: PolicyStatus
    source_type: PolicySourceType
    effective_from: date
    effective_to: date | None
    body: str
    source_path: str | None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Parse a leading ``---`` front-matter block into a dict plus the body."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    body = "\n".join(lines[end + 1 :]).strip() + "\n"
    return meta, body


def _parse_fixture_file(path: Path) -> PolicySource:
    meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
    effective_to = meta.get("effective_to")
    return PolicySource(
        topic=meta["topic"],
        title=meta["title"],
        description=meta.get("description", meta["title"]),
        version=int(meta.get("version", "1")),
        status=PolicyStatus(meta.get("status", "active")),
        source_type=PolicySourceType(meta.get("source_type", "official_policy")),
        effective_from=date.fromisoformat(meta["effective_from"]),
        effective_to=date.fromisoformat(effective_to) if effective_to else None,
        body=body,
        source_path=f"data/policies/fixtures/{path.name}",
    )


def fixture_sources() -> list[PolicySource]:
    """Load the isolated fixture policy sources (hostile, future), if present."""
    fixtures_dir = get_policies_dir() / "fixtures"
    if not fixtures_dir.is_dir():
        return []
    return [_parse_fixture_file(p) for p in sorted(fixtures_dir.glob("*.md"))]
