"""Markdown-structure-aware policy chunker (``chunker-v1``).

Splits on heading hierarchy and blank-line-separated blocks (paragraphs, bullet/numbered
lists), never splitting a single block — so a numbered rule is never cut in half. Blocks
are greedily packed up to ``CHUNK_MAX_CHARS``; tiny trailing chunks are merged into the
previous chunk. Output is deterministic for identical input.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.retrieval.constants import CHUNK_MAX_CHARS, CHUNK_MIN_CHARS

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


@dataclass(frozen=True, slots=True)
class ChunkSpec:
    chunk_index: int
    section_path: str
    heading: str | None
    body: str
    search_text: str
    token_count: int
    character_count: int
    content_hash: str


@dataclass(slots=True)
class _Section:
    heading: str | None
    section_path: str
    blocks: list[str]


def _split_sections(text: str, title: str) -> list[_Section]:
    stack: list[tuple[int, str]] = []  # (level, heading)
    sections: list[_Section] = []
    current: _Section | None = None
    buffer: list[str] = []

    def flush_block() -> None:
        if current is None or not buffer:
            return
        block = "\n".join(buffer).strip()
        if block:
            current.blocks.append(block)

    def path_from_stack() -> str:
        segments: list[str] = [title]
        for _, heading in stack:
            if heading != segments[-1]:  # avoid "Title > Title" when H1 == title
                segments.append(heading)
        return " > ".join(segments)

    for raw_line in text.splitlines():
        heading_match = _HEADING_RE.match(raw_line.strip())
        if heading_match:
            flush_block()
            buffer = []
            level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            current = _Section(
                heading=heading, section_path=path_from_stack(), blocks=[]
            )
            sections.append(current)
        elif raw_line.strip() == "":
            flush_block()
            buffer = []
        else:
            if current is None:
                current = _Section(heading=None, section_path=title, blocks=[])
                sections.append(current)
            buffer.append(raw_line.rstrip())
    flush_block()
    return [s for s in sections if s.blocks]


def _pack_blocks(blocks: list[str]) -> list[str]:
    """Greedily pack blocks up to the max size, never splitting a block."""
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for block in blocks:
        block_len = len(block)
        if current and length + block_len + 2 > CHUNK_MAX_CHARS:
            chunks.append("\n\n".join(current))
            current = []
            length = 0
        current.append(block)
        length += block_len + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk_markdown(body: str, *, title: str) -> list[ChunkSpec]:
    """Chunk a policy markdown ``body`` into deterministic ``ChunkSpec`` rows."""
    raw: list[tuple[str, str | None, str]] = []  # (section_path, heading, chunk_body)
    for section in _split_sections(body, title):
        for chunk_body in _pack_blocks(section.blocks):
            raw.append((section.section_path, section.heading, chunk_body))

    # Merge a chunk smaller than the minimum into the previous chunk.
    merged: list[tuple[str, str | None, str]] = []
    for section_path, heading, chunk_body in raw:
        if merged and len(chunk_body) < CHUNK_MIN_CHARS:
            prev_path, prev_heading, prev_body = merged[-1]
            merged[-1] = (prev_path, prev_heading, f"{prev_body}\n\n{chunk_body}")
        else:
            merged.append((section_path, heading, chunk_body))

    specs: list[ChunkSpec] = []
    for index, (section_path, heading, chunk_body) in enumerate(merged):
        search_text = f"{section_path}\n{chunk_body}"
        specs.append(
            ChunkSpec(
                chunk_index=index,
                section_path=section_path,
                heading=heading,
                body=chunk_body,
                search_text=search_text,
                token_count=len(search_text.split()),
                character_count=len(chunk_body),
                content_hash=hashlib.sha256(chunk_body.encode("utf-8")).hexdigest(),
            )
        )
    return specs
